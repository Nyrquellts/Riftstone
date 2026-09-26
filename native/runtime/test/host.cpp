#include "runtime.h"
#include "checked.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <malloc.h>
#include <vector>
#include <fstream>
#include <atomic>

static_assert(sizeof(void*)==4,"this harness must exercise the actual PE32 ABI");
int failures=0,checks=0;
std::atomic<bool> stop_worker=false;
std::atomic<int> worker_errors=0,worker_reads=0;
DWORD WINAPI concurrent_reads(void* path) {
    while (!stop_worker.load()) {
        HANDLE handle=CreateFileW(static_cast<const wchar_t*>(path),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
        char data[32]{}; DWORD count=0;
        if (handle==INVALID_HANDLE_VALUE || !ReadFile(handle,data,sizeof data,&count,nullptr) ||
            !((count==6&&!std::memcmp(data,"abcdef",6)) || (count==13&&!std::memcmp(data,"BASE-ORIGINAL",13)))) ++worker_errors;
        if (handle!=INVALID_HANDLE_VALUE && !CloseHandle(handle)) ++worker_errors;
        ++worker_reads;
    }
    return 0;
}
void check(bool condition,const char* message) {
    char trace[2];
    if (GetEnvironmentVariableA("RS_HOST_TRACE",trace,sizeof trace)) { std::printf("CHECK %d %s\n",checks+1,message); std::fflush(stdout); }
    ++checks; if (!condition) { ++failures; std::printf("FAIL %s (winerror=%lu)\n",message,GetLastError()); }
}

void checked_tests() {
    size_t result;
    check(!riftstone::checked_add(SIZE_MAX,1,result),"size addition overflow rejected");
    check(!riftstone::checked_mul(SIZE_MAX,2,result),"size multiplication overflow rejected");
    check(riftstone::pointer_range(0x7ffffff0,16,0x7fffffff),"signed pointer boundary inclusive");
    check(!riftstone::pointer_range(0x7ffffff0,17,0x7fffffff),"signed pointer crossing refused");
    check(!riftstone::pointer_range(0xfffffff0,17,0xffffffff),"32-bit pointer wrap refused");
    std::vector<uint8_t> image={0x90,0xaa,0x11,0xcc,0x90};
    size_t offset=0;
    check(riftstone::unique_signature(image,{{0xaa,0,0xcc},{true,false,true}},offset)==riftstone::LocateResult::unique&&offset==1,"unique masked signature");
    check(riftstone::unique_signature(image,{{0x90},{true}},offset)==riftstone::LocateResult::ambiguous,"ambiguous signature refused");
    check(riftstone::unique_signature(image,{{0},{false}},offset)==riftstone::LocateResult::invalid,"all-wildcard signature refused");
    const auto original=image;
    check(!riftstone::apply_verified(image,{{1,{0xaa},{0xbb}},{3,{0xdd},{0xee}}})&&image==original,"failed preflight leaves entire image unchanged");
    check(!riftstone::apply_verified(image,{{1,{0xaa,0x11},{0xbb,0x22}},{2,{0x11},{0x33}}})&&image==original,"overlapping patches refused");
    check(riftstone::apply_verified(image,{{1,{0xaa},{0xbb}},{3,{0xcc},{0xee}}})&&image[1]==0xbb&&image[3]==0xee,"verified patches applied together");
    riftstone::Expansion plan;
    check(riftstone::expansion_plan(0x1b950,0x844,10,0x20,64,64,0x7fffffff,plan)&&plan.bytes==0x1c150,"measured enemy storage plan stays within checked bounds");
    check(!riftstone::expansion_plan(0x1b950,0x844,10,0x20,256,64,0x7fffffff,plan),"unverified 256-cap plan refused");
    riftstone::SlotArena arena(4,256);
    check(arena.grow(2),"staging arena grows");
    riftstone::SlotArena::Handle first,second,recycled;
    check(arena.acquire(first)&&arena.acquire(second)&&!arena.acquire(recycled),"staging arena capacity enforced");
    const uint8_t input[]={1,2,3,4}; uint8_t output[4]{};
    check(arena.write(first,input)&&arena.grow(256)&&arena.read(first,output)&&!std::memcmp(input,output,4),"handle survives backing storage expansion");
    check(arena.release(first)&&arena.acquire(recycled)&&!arena.read(first,output),"stale handle rejected after reuse");
    check(!arena.grow(257),"arena maximum enforced");
    riftstone::SlotArena skill_staging(4,6);
    check(skill_staging.grow(6)&&!skill_staging.grow(7),"six-slot staging has checked capacity without claiming Warrior hooks");
}

int wmain(int argc,wchar_t** argv) {
    SetErrorMode(SEM_NOGPFAULTERRORBOX|SEM_FAILCRITICALERRORS);
    if (argc<3) return 2;
    checked_tests();
    HANDLE heap=HeapCreate(0,0,0);
    RsConfig config{sizeof(RsConfig),1,argv[1],argv[2],1024*1024,UINTPTR_MAX,argc>3&&!wcscmp(argv[3],L"mimalloc")?1:0,heap};
    wchar_t prohibited_log[32768]; swprintf_s(prohibited_log,L"%s\\base.bin",argv[1]);
    auto prohibited=config; prohibited.log_path=prohibited_log;
    check(RiftstoneRuntime_Initialize(&prohibited)==RS_BAD_ARGUMENT,"log destination cannot overwrite or append to base assets");
    if (argc>5) {
        prohibited.log_path=argv[5];
        check(RiftstoneRuntime_Initialize(&prohibited)==RS_BAD_ARGUMENT,"outside hardlink log cannot append to a base asset");
    }
    const int init=RiftstoneRuntime_Initialize(&config);
    if (init==RS_UNAVAILABLE) { std::puts("MIMALLOC_UNAVAILABLE"); return 3; }
    check(init==RS_OK,"explicit initialization");
    check(RiftstoneRuntime_AddFile(L"base.bin/child", "x",1,1)==RS_BAD_ARGUMENT,"virtual child cannot turn a base file into a directory");
    void* direct=RiftstoneRuntime_Allocate(37,64);
    check(direct&&reinterpret_cast<uintptr_t>(direct)%64==0,"aligned owned allocation");
    check(RiftstoneRuntime_Shutdown()==RS_BUSY,"shutdown cannot strand an owned allocation");
    int foreign=0;
    check(RiftstoneRuntime_Free(&foreign)==RS_NOT_OWNED,"foreign pointer never freed");
    check(RiftstoneRuntime_Free(direct)==RS_OK&&RiftstoneRuntime_Free(direct)==RS_NOT_OWNED,"owned release and double-release refusal");
    check(!RiftstoneRuntime_Allocate(SIZE_MAX,16)&&!RiftstoneRuntime_Allocate(1,3),"overflow and invalid alignment refused");
    check(RiftstoneRuntime_GameProfile("warrior-six-skills")==RS_UNAVAILABLE,"unmeasured Warrior profile fails closed");
    check(RiftstoneRuntime_GameProfile("item-icon-cache")==RS_UNAVAILABLE,"unmeasured icon profile fails closed");
    if (argc>4) {
        std::ifstream stream(argv[4],std::ios::binary);
        std::vector<char> data((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
        check(RiftstoneRuntime_LoadBundle(data.data(),data.size())==RS_OK,"Python semantic-merge bundle loads transactionally");
        check(RiftstoneRuntime_LoadBundle(data.data(),data.size()-1)==RS_BAD_ARGUMENT,"truncated bundle rejected without altering snapshot");
    }
    check(RiftstoneRuntime_AddFile(L"file.bin","LOW",3,1)==RS_OK,"low overlay layer");
    check(RiftstoneRuntime_AddFile(L"file.bin","abcdef",6,2)==RS_OK,"high overlay priority");
    check(RiftstoneRuntime_AddFile(L"folder/new.txt","new",3,1)==RS_OK,"virtual directory entry");
    check(RiftstoneRuntime_AddFile(L"../escape","x",1,1)==RS_BAD_ARGUMENT,"relative escape rejected");
    check(RiftstoneRuntime_AddFile(L"nul.txt","x",1,1)==RS_BAD_ARGUMENT,"device path rejected");
    check(RiftstoneRuntime_AddFile(L"folder","x",1,1)==RS_BAD_ARGUMENT,"file-directory collision rejected");
    int enabled=RiftstoneRuntime_EnableHooks(GetModuleHandleW(nullptr));
    if (enabled==RS_UNAVAILABLE) {
        check(RiftstoneRuntime_Shutdown()==RS_OK,"unhooked shutdown"); HeapDestroy(heap);
        std::printf("CORE_CHECKS=%d FAILURES=%d HOOKS_UNAVAILABLE\n",checks,failures); return failures?1:3;
    }
    check(enabled==RS_OK,"actual MinHook activation in this synthetic host");
    if (enabled!=RS_OK) return 1;
    if (argc>4) {
        wchar_t merged[32768]; swprintf_s(merged,L"%s\\merged.json",argv[1]);
        HANDLE merged_file=CreateFileW(merged,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
        char text[128]{}; DWORD got=0;
        check(merged_file!=INVALID_HANDLE_VALUE&&ReadFile(merged_file,text,sizeof(text)-1,&got,nullptr)&&std::strstr(text,"\"a\": 3")&&std::strstr(text,"\"b\": 4"),"actual ReadFile serves field-merged Python snapshot in memory");
        if (merged_file!=INVALID_HANDLE_VALUE) CloseHandle(merged_file);
    }
    check(RiftstoneRuntime_AddFile(L"late","x",1,3)==RS_BUSY,"mounted snapshot is frozen");
    wchar_t file[32768],base[32768],pattern[32768],virtualdir[32768];
    swprintf_s(file,L"%s\\file.bin",argv[1]); swprintf_s(base,L"%s\\base.bin",argv[1]);
    swprintf_s(pattern,L"%s\\*",argv[1]); swprintf_s(virtualdir,L"%s\\folder",argv[1]);
    HANDLE handle=CreateFileW(file,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    check(handle!=INVALID_HANDLE_VALUE,"CreateFileW redirected to memory");
    HANDLE duplicate=nullptr;
    check(!DuplicateHandle(GetCurrentProcess(),handle,GetCurrentProcess(),&duplicate,0,FALSE,DUPLICATE_SAME_ACCESS|DUPLICATE_CLOSE_SOURCE)&&GetLastError()==ERROR_NOT_SUPPORTED,"virtual duplicate-close-source cannot orphan ownership");
    char output[16]{}; DWORD count=0;
    check(ReadFile(handle,output,2,&count,nullptr)&&count==2&&!std::memcmp(output,"ab",2),"ReadFile streams selected overlay bytes");
    LARGE_INTEGER size{},distance{}; distance.QuadPart=-1;
    check(GetFileSizeEx(handle,&size)&&size.QuadPart==6&&GetFileSize(handle,nullptr)==6,"both file size APIs agree");
    check(SetFilePointerEx(handle,distance,nullptr,FILE_END)&&ReadFile(handle,output,8,&count,nullptr)&&count==1&&output[0]=='f',"seek from EOF and short read");
    check(ReadFile(handle,output,8,&count,nullptr)&&count==0,"EOF is a successful zero-byte read");
    check(SetFilePointer(handle,-10,nullptr,FILE_BEGIN)==INVALID_SET_FILE_POINTER&&GetLastError()==ERROR_NEGATIVE_SEEK,"negative seek rejected");
    check(SetFilePointer(handle,0,nullptr,FILE_BEGIN)==0,"legacy seek supported");
    OVERLAPPED overlapped{};
    check(!ReadFile(handle,output,1,&count,&overlapped)&&GetLastError()==ERROR_NOT_SUPPORTED,"overlapped reads explicitly refused");
    check(!WriteFile(handle,"x",1,&count,nullptr)&&GetLastError()==ERROR_ACCESS_DENIED,"writes cannot mutate memory view");
    check(!CreateFileMappingW(handle,nullptr,PAGE_READONLY,0,0,nullptr)&&GetLastError()==ERROR_NOT_SUPPORTED,"unsupported mapping cannot treat virtual handle as disk handle");
    check(CreateFileW(file,GENERIC_READ,0,nullptr,OPEN_EXISTING,0,nullptr)==INVALID_HANDLE_VALUE&&GetLastError()==ERROR_SHARING_VIOLATION,"share mode enforced");
    check(RiftstoneRuntime_DisableHooks()==RS_BUSY,"hooks cannot disable with open virtual handles");
    check(CloseHandle(handle),"CloseHandle retires memory stream");
    check(CreateFileW(file,GENERIC_WRITE,0,nullptr,OPEN_EXISTING,0,nullptr)==INVALID_HANDLE_VALUE&&GetLastError()==ERROR_ACCESS_DENIED,"overlay write open denied");
    check(CreateFileW(base,GENERIC_WRITE,0,nullptr,OPEN_EXISTING,0,nullptr)==INVALID_HANDLE_VALUE&&GetLastError()==ERROR_ACCESS_DENIED,"base write open denied");
    check(GetFileAttributesW(file)&FILE_ATTRIBUTE_READONLY,"virtual file attributes");
    check(GetFileAttributesW(virtualdir)&FILE_ATTRIBUTE_DIRECTORY,"virtual directory attributes");
    char ansi[32768]; WideCharToMultiByte(CP_ACP,0,file,-1,ansi,sizeof ansi,nullptr,nullptr);
    handle=CreateFileA(ansi,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
    check(handle!=INVALID_HANDLE_VALUE&&ReadFile(handle,output,6,&count,nullptr)&&count==6&&!std::memcmp(output,"abcdef",6),"CreateFileA matches wide overlay lookup"); CloseHandle(handle);
    handle=CreateFileW(base,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
    check(handle!=INVALID_HANDLE_VALUE&&ReadFile(handle,output,16,&count,nullptr)&&count==7&&!std::memcmp(output,"VANILLA",7),"base read passes through unchanged"); CloseHandle(handle);
    WIN32_FIND_DATAW data{}; HANDLE find=FindFirstFileW(pattern,&data);
    bool saw_file=false,saw_base=false,saw_folder=false; int file_count=0;
    if (find!=INVALID_HANDLE_VALUE) {
        do { if (!_wcsicmp(data.cFileName,L"file.bin")) { saw_file=data.nFileSizeLow==6; ++file_count; }
             if (!_wcsicmp(data.cFileName,L"base.bin")) saw_base=true;
             if (!_wcsicmp(data.cFileName,L"folder")) saw_folder=(data.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)!=0;
        } while (FindNextFileW(find,&data));
        check(GetLastError()==ERROR_NO_MORE_FILES,"enumeration exhaustion error");
        check(FindClose(find),"FindClose retires virtual enumeration");
    }
    check(saw_file&&saw_base&&saw_folder&&file_count==1,"enumeration unions base and overlays without duplicates");
    void* p=HeapAlloc(heap,HEAP_ZERO_MEMORY,16);
    bool zero=true; if (p) for (int i=0;i<16;++i) zero&=static_cast<unsigned char*>(p)[i]==0;
    check(p&&zero&&HeapSize(heap,0,p)==16,"selected HeapAlloc and HeapSize redirect together");
    RsStats owned{sizeof(RsStats)};
    check(RiftstoneRuntime_Stats(&owned)==RS_OK&&owned.owned_blocks==1,"HeapAlloc interception reaches ownership backend");
    check(!HeapFree(GetProcessHeap(),0,p)&&GetLastError()==ERROR_INVALID_PARAMETER,"cross-heap free refused");
    check(!HeapDestroy(heap)&&GetLastError()==ERROR_BUSY,"heap cannot be destroyed with redirected blocks");
    if (p) { std::memcpy(p,"ABCD",4); p=HeapReAlloc(heap,HEAP_ZERO_MEMORY,p,32); }
    check(p&&HeapSize(heap,0,p)==32&&!std::memcmp(p,"ABCD",4)&&static_cast<unsigned char*>(p)[31]==0,"owned heap realloc preserves data and zeroes growth");
    check(HeapFree(heap,0,p),"owned heap free");
    void* ordinary=HeapAlloc(GetProcessHeap(),0,12);
    check(ordinary&&HeapFree(GetProcessHeap(),0,ordinary),"unselected heap allocation/free remain paired");
    void* c=std::malloc(24);
    check(c&&_msize(c)>=24,"CRT malloc/msize pair");
    check(RiftstoneRuntime_Stats(&owned)==RS_OK&&owned.owned_blocks==1,"CRT malloc interception reaches ownership backend");
    if (c) { std::memcpy(c,"CRT",3); c=std::realloc(c,48); }
    check(c&&!std::memcmp(c,"CRT",3),"CRT realloc preserves owned content"); std::free(c);
    void* pages=VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    check(pages&&VirtualFree(pages,0,MEM_RELEASE),"VirtualAlloc/Free reserve/commit/release semantics preserved");
    RsStats stats{sizeof(RsStats)};
    check(RiftstoneRuntime_Stats(&stats)==RS_OK&&stats.owned_blocks==0&&stats.virtual_handles==0&&stats.redirected_opens>=2&&stats.passthrough_virtual_alloc>=1,"all runtime ownership balances close");
    HANDLE worker=CreateThread(nullptr,0,concurrent_reads,file,0,nullptr);
    check(worker!=nullptr,"concurrent VFS reader started");
    const auto until=GetTickCount64()+5000;
    while (worker_reads.load()<100&&GetTickCount64()<until) Sleep(0);
    int disabled=RS_BUSY;
    while (disabled==RS_BUSY&&GetTickCount64()<until) disabled=RiftstoneRuntime_DisableHooks();
    stop_worker=true;
    if (worker) { WaitForSingleObject(worker,5000); CloseHandle(worker); }
    if (disabled==RS_BUSY) disabled=RiftstoneRuntime_DisableHooks();
    check(disabled==RS_OK,"explicit hook disable during concurrent reads");
    check(worker_reads.load()>=100&&worker_errors.load()==0,"teardown cannot strand an in-flight virtual open");
    check(RiftstoneRuntime_Shutdown()==RS_OK,"explicit shutdown outside loader lock");
    HeapDestroy(heap);
    std::printf("NATIVE_CHECKS=%d FAILURES=%d\n",checks,failures);
    return failures?1:0;
}
