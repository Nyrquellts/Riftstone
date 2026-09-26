#define RIFTSTONE_RUNTIME_BUILD
#include "runtime.h"
#include "checked.hpp"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <cwchar>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <intrin.h>
#ifdef RS_HAVE_MINHOOK
#include <MinHook.h>
#endif
#ifdef RS_HAVE_MIMALLOC
#include <mimalloc.h>
#endif

namespace {
using Bytes = std::vector<unsigned char>;
struct ComparePath { bool operator()(const std::wstring& a, const std::wstring& b) const noexcept {
    return CompareStringOrdinal(a.c_str(), static_cast<int>(a.size()), b.c_str(), static_cast<int>(b.size()), TRUE) == CSTR_LESS_THAN;
}};
struct File { std::shared_ptr<const Bytes> data; int priority; };
struct Stream { std::shared_ptr<const Bytes> data; std::wstring path; uint64_t position=0; DWORD access=0, share=0; };
struct Search { std::vector<WIN32_FIND_DATAW> entries; size_t next=0; };
enum class Owner { direct, heap, crt };
struct Allocation { void* raw; size_t bytes; Owner owner; HANDLE heap; };
std::recursive_mutex mutex;
std::map<std::wstring, File, ComparePath> files;
std::map<HANDLE, Stream> streams;
std::map<HANDLE, Search> searches;
std::map<void*, Allocation> allocations;
std::wstring root;
HANDLE private_heap=nullptr, log_file=INVALID_HANDLE_VALUE, selected_heap=nullptr;
size_t allocation_limit=0;
uintptr_t pointer_ceiling=0, caller_begin=0, caller_end=0;
DWORD owner_thread=0;
std::atomic<bool> initialized=false, hooks_enabled=false;
bool frozen=false, hooks_created=false, use_mimalloc=false;
uint64_t owned_bytes=0, redirects=0, virtual_alloc_calls=0;
// Global heap hooks can run while the loader is creating a new thread's static
// TLS vector. Compiler thread_local access is unsafe at that point. Reserve a
// primary Win32 TLS slot before activation; expansion slots may allocate.
std::atomic<DWORD> reentry_slot=TLS_OUT_OF_INDEXES;
bool inside() noexcept {
    const DWORD slot=reentry_slot.load();
    if (slot==TLS_OUT_OF_INDEXES) return false;
    const DWORD error=GetLastError();
    const bool value=TlsGetValue(slot)!=nullptr;
    SetLastError(error); return value;
}
struct Guard {
    bool old=inside();
    Guard() noexcept { set(true); }
    ~Guard() { set(old); }
    static void set(bool value) noexcept {
        const DWORD slot=reentry_slot.load();
        if (slot==TLS_OUT_OF_INDEXES) return;
        const DWORD error=GetLastError();
        TlsSetValue(slot,value?reinterpret_cast<void*>(1):nullptr);
        SetLastError(error);
    }
};
#ifdef RS_HAVE_MIMALLOC
mi_heap_t* mi_heap=nullptr;
mi_arena_id_t mi_arena=0;
bool mi_arena_ready=false;
#endif

decltype(&::CreateFileW) real_CreateFileW=::CreateFileW;
decltype(&::CreateFileA) real_CreateFileA=::CreateFileA;
decltype(&::ReadFile) real_ReadFile=::ReadFile;
decltype(&::CloseHandle) real_CloseHandle=::CloseHandle;
decltype(&::DuplicateHandle) real_DuplicateHandle=::DuplicateHandle;
decltype(&::GetFileAttributesW) real_GetFileAttributesW=::GetFileAttributesW;
decltype(&::FindFirstFileW) real_FindFirstFileW=::FindFirstFileW;
decltype(&::FindNextFileW) real_FindNextFileW=::FindNextFileW;
decltype(&::FindClose) real_FindClose=::FindClose;
decltype(&::SetFilePointerEx) real_SetFilePointerEx=::SetFilePointerEx;
decltype(&::SetFilePointer) real_SetFilePointer=::SetFilePointer;
decltype(&::GetFileSizeEx) real_GetFileSizeEx=::GetFileSizeEx;
decltype(&::GetFileSize) real_GetFileSize=::GetFileSize;
decltype(&::WriteFile) real_WriteFile=::WriteFile;
decltype(&::FlushFileBuffers) real_FlushFileBuffers=::FlushFileBuffers;
decltype(&::CreateFileMappingW) real_CreateFileMappingW=::CreateFileMappingW;
decltype(&::HeapAlloc) real_HeapAlloc=::HeapAlloc;
decltype(&::HeapFree) real_HeapFree=::HeapFree;
decltype(&::HeapReAlloc) real_HeapReAlloc=::HeapReAlloc;
decltype(&::HeapSize) real_HeapSize=::HeapSize;
decltype(&::HeapDestroy) real_HeapDestroy=::HeapDestroy;
decltype(&::VirtualAlloc) real_VirtualAlloc=::VirtualAlloc;
decltype(&::VirtualFree) real_VirtualFree=::VirtualFree;
using MallocFn = void* (__cdecl*)(size_t);
using FreeFn = void (__cdecl*)(void*);
using ReallocFn = void* (__cdecl*)(void*, size_t);
using MsizeFn = size_t (__cdecl*)(void*);
MallocFn real_malloc=nullptr;
FreeFn real_free=nullptr;
ReallocFn real_realloc=nullptr;
MsizeFn real_msize=nullptr;
struct HandleScope {
    HANDLE handle;
    BOOL (WINAPI* close)(HANDLE);
    ~HandleScope() { const DWORD error=GetLastError(); close(handle); SetLastError(error); }
};

bool eligible(void* address) noexcept {
    const auto p=reinterpret_cast<uintptr_t>(address);
    return initialized && hooks_enabled && p>=caller_begin && p<caller_end;
}
void log(const char* text) noexcept {
    if (log_file==INVALID_HANDLE_VALUE) return;
    DWORD ignored;
    real_WriteFile(log_file, text, static_cast<DWORD>(std::strlen(text)), &ignored, nullptr);
    real_WriteFile(log_file, "\r\n", 2, &ignored, nullptr);
}
bool equal(const std::wstring& a, const std::wstring& b) noexcept {
    return CompareStringOrdinal(a.c_str(), static_cast<int>(a.size()), b.c_str(), static_cast<int>(b.size()), TRUE)==CSTR_EQUAL;
}
bool relative_valid(const std::wstring& value) {
    if (value.empty() || value.size()>30000 || value.front()==L'\\' || value.front()==L'/') return false;
    size_t at=0;
    while (at<value.size()) {
        size_t end=value.find_first_of(L"\\/",at); if (end==std::wstring::npos) end=value.size();
        auto part=value.substr(at,end-at);
        if (part.empty() || part==L"." || part==L".." || part.back()==L'.' || part.back()==L' ') return false;
        for (auto ch:part) if (ch<32 || std::wcschr(L"<>:\"|?*",ch)) return false;
        auto stem=part.substr(0,part.find(L'.'));
        if (equal(stem,L"CON") || equal(stem,L"PRN") || equal(stem,L"AUX") || equal(stem,L"NUL")) return false;
        if (stem.size()==4 && (equal(stem.substr(0,3),L"COM") || equal(stem.substr(0,3),L"LPT")) && stem[3]>=L'1' && stem[3]<=L'9') return false;
        at=end+1;
    }
    return value.back()!=L'/' && value.back()!=L'\\';
}
std::wstring canonical(const wchar_t* name) {
    if (!name || !*name) return {};
    const DWORD need=GetFullPathNameW(name,0,nullptr,nullptr);
    if (!need || need>32768) return {};
    std::wstring out(need,L'\0');
    const DWORD got=GetFullPathNameW(name,need,out.data(),nullptr);
    if (!got || got>=need) return {};
    out.resize(got); std::replace(out.begin(),out.end(),L'/',L'\\'); return out;
}
std::wstring physical(const std::wstring& path) {
    HANDLE handle=real_CreateFileW(path.c_str(),0,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if (handle==INVALID_HANDLE_VALUE) return {};
    HandleScope scope{handle,real_CloseHandle};
    DWORD need=GetFinalPathNameByHandleW(handle,nullptr,0,FILE_NAME_NORMALIZED);
    std::wstring result;
    if (need && need<32768) {
        result.resize(need);
        DWORD got=GetFinalPathNameByHandleW(handle,result.data(),need,FILE_NAME_NORMALIZED);
        if (got && got<need) result.resize(got); else result.clear();
    }
    return result;
}
bool external_log(const wchar_t* path,const std::wstring& base) {
    if (!path || !*path) return true;
    const auto full=canonical(path);
    HANDLE existing=real_CreateFileW(path,0,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if (existing!=INVALID_HANDLE_VALUE) {
        HandleScope scope{existing,real_CloseHandle};
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(existing,&info) || info.nNumberOfLinks!=1) return false;
    } else if (GetLastError()!=ERROR_FILE_NOT_FOUND) return false;
    auto base_physical=physical(base);
    if (full.empty() || base_physical.empty()) return false;
    if (base_physical.back()!=L'\\') base_physical+=L'\\';
    auto target=physical(full);
    if (target.empty()) {
        const auto slash=full.find_last_of(L'\\');
        if (slash==std::wstring::npos) return false;
        auto parent=physical(full.substr(0,slash));
        if (parent.empty()) return false;
        target=parent+L'\\'+full.substr(slash+1);
    }
    return !(target.size()>=base_physical.size() && equal(target.substr(0,base_physical.size()),base_physical));
}
bool under_root(const wchar_t* name, std::wstring& relative) {
    auto full=canonical(name);
    if (full.size()<root.size() || !equal(full.substr(0,root.size()),root)) return false;
    relative=full.substr(root.size());
    return relative.empty() || relative_valid(relative);
}
bool virtual_directory(const std::wstring& relative) {
    auto prefix=relative; if (!prefix.empty()) prefix+=L'\\';
    for (const auto& [path,file]:files) {
        (void)file;
        if (path.size()>prefix.size() && equal(path.substr(0,prefix.size()),prefix)) return true;
    }
    return false;
}
bool base_compatible_file(const std::wstring& path) {
    const DWORD attributes=real_GetFileAttributesW((root+path).c_str());
    if (attributes!=INVALID_FILE_ATTRIBUTES && (attributes&FILE_ATTRIBUTE_DIRECTORY)) return false;
    for (auto slash=path.find(L'\\');slash!=std::wstring::npos;slash=path.find(L'\\',slash+1)) {
        const DWORD parent=real_GetFileAttributesW((root+path.substr(0,slash)).c_str());
        if (parent!=INVALID_FILE_ATTRIBUTES && !(parent&FILE_ATTRIBUTE_DIRECTORY)) return false;
    }
    return true;
}
void* allocate(size_t bytes, size_t alignment, Owner owner, HANDLE heap) {
    if (!initialized || !alignment || (alignment&(alignment-1)) || alignment>65536 || bytes>allocation_limit) return nullptr;
    size_t total;
    if (!riftstone::checked_add(std::max<size_t>(bytes,1),alignment-1,total)) return nullptr;
    void* raw=nullptr;
#ifdef RS_HAVE_MIMALLOC
    if (use_mimalloc) {
        if (GetCurrentThreadId()!=owner_thread) return nullptr;
        raw=mi_heap_malloc(mi_heap,total);
    } else
#endif
        raw=real_HeapAlloc(private_heap,0,total);
    if (!raw) return nullptr;
    const auto begin=reinterpret_cast<uintptr_t>(raw);
    const auto aligned=(begin+alignment-1)&~static_cast<uintptr_t>(alignment-1);
    auto release=[&]() {
#ifdef RS_HAVE_MIMALLOC
        if (use_mimalloc) mi_free(raw); else
#endif
            real_HeapFree(private_heap,0,raw);
    };
    if (aligned<begin || !riftstone::pointer_range(aligned,std::max<size_t>(bytes,1),pointer_ceiling)) { release(); return nullptr; }
    auto pointer=reinterpret_cast<void*>(aligned);
    try { allocations.emplace(pointer,Allocation{raw,bytes,owner,heap}); }
    catch (...) { release(); throw; }
    owned_bytes+=bytes; return pointer;
}
void release(std::map<void*,Allocation>::iterator it) noexcept {
    const auto record=it->second;
    owned_bytes-=record.bytes; allocations.erase(it);
#ifdef RS_HAVE_MIMALLOC
    if (use_mimalloc) mi_free(record.raw); else
#endif
        real_HeapFree(private_heap,0,record.raw);
}
void* resize_owned(std::map<void*,Allocation>::iterator it, size_t bytes, bool zero) {
    const auto old=it->second; void* pointer=allocate(bytes,16,old.owner,old.heap);
    if (!pointer) return nullptr;
    std::memcpy(pointer,it->first,std::min(old.bytes,bytes));
    if (zero && bytes>old.bytes) std::memset(static_cast<unsigned char*>(pointer)+old.bytes,0,bytes-old.bytes);
    release(it); return pointer;
}

HANDLE open_virtual(const std::wstring& relative, DWORD access,DWORD share,LPSECURITY_ATTRIBUTES sa,DWORD disposition,DWORD flags) {
    auto file=files.find(relative);
    if (file==files.end()) { SetLastError(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE_VALUE; }
    constexpr DWORD writes=GENERIC_WRITE|GENERIC_ALL|FILE_WRITE_DATA|FILE_APPEND_DATA|FILE_WRITE_EA|FILE_WRITE_ATTRIBUTES|DELETE|WRITE_DAC|WRITE_OWNER;
    if (access&writes || disposition!=OPEN_EXISTING || flags&FILE_FLAG_DELETE_ON_CLOSE) { SetLastError(ERROR_ACCESS_DENIED); return INVALID_HANDLE_VALUE; }
    constexpr DWORD allowed_flags=FILE_ATTRIBUTE_NORMAL|FILE_FLAG_SEQUENTIAL_SCAN|FILE_FLAG_RANDOM_ACCESS;
    if ((access&~(GENERIC_READ|FILE_READ_DATA|FILE_READ_ATTRIBUTES|SYNCHRONIZE)) || (share&~(FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE)) || (flags&~allowed_flags) || sa) {
        SetLastError(ERROR_NOT_SUPPORTED); return INVALID_HANDLE_VALUE;
    }
    const bool reads=(access&(GENERIC_READ|FILE_READ_DATA))!=0;
    for (const auto& [handle,stream]:streams) {
        (void)handle;
        if (equal(stream.path,relative) && ((reads && !(stream.share&FILE_SHARE_READ)) || ((stream.access&(GENERIC_READ|FILE_READ_DATA)) && !(share&FILE_SHARE_READ)))) {
            SetLastError(ERROR_SHARING_VIOLATION); return INVALID_HANDLE_VALUE;
        }
    }
    HANDLE handle=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    if (!handle) return INVALID_HANDLE_VALUE;
    try { streams.emplace(handle,Stream{file->second.data,relative,0,access,share}); }
    catch (...) { real_CloseHandle(handle); throw; }
    ++redirects; return handle;
}

HANDLE WINAPI hook_CreateFileW(LPCWSTR name,DWORD access,DWORD share,LPSECURITY_ATTRIBUTES sa,DWORD disposition,DWORD flags,HANDLE templ) noexcept {
    if (inside() || !eligible(_ReturnAddress())) return real_CreateFileW(name,access,share,sa,disposition,flags,templ);
    Guard guard;
    try { std::lock_guard lock(mutex); std::wstring rel;
        if (!initialized || !hooks_enabled) return real_CreateFileW(name,access,share,sa,disposition,flags,templ);
        if (under_root(name,rel)) {
            if ((access&(GENERIC_WRITE|GENERIC_ALL|FILE_WRITE_DATA|FILE_APPEND_DATA|FILE_WRITE_EA|FILE_WRITE_ATTRIBUTES|DELETE|WRITE_DAC|WRITE_OWNER)) || disposition!=OPEN_EXISTING || (flags&FILE_FLAG_DELETE_ON_CLOSE)) { SetLastError(ERROR_ACCESS_DENIED); return INVALID_HANDLE_VALUE; }
            if (files.contains(rel)) return open_virtual(rel,access,share,sa,disposition,flags);
        }
    } catch (...) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return INVALID_HANDLE_VALUE; }
    return real_CreateFileW(name,access,share,sa,disposition,flags,templ);
}
HANDLE WINAPI hook_CreateFileA(LPCSTR name,DWORD access,DWORD share,LPSECURITY_ATTRIBUTES sa,DWORD disposition,DWORD flags,HANDLE templ) noexcept {
    if (inside() || !eligible(_ReturnAddress())) return real_CreateFileA(name,access,share,sa,disposition,flags,templ);
    Guard guard;
    try { const int count=name?MultiByteToWideChar(CP_ACP,0,name,-1,nullptr,0):0;
        if (count>0) { std::wstring wide(count,L'\0'); MultiByteToWideChar(CP_ACP,0,name,-1,wide.data(),count);
            std::lock_guard lock(mutex); std::wstring rel;
            if (!initialized || !hooks_enabled) return real_CreateFileA(name,access,share,sa,disposition,flags,templ);
            if (under_root(wide.c_str(),rel)) {
                if ((access&(GENERIC_WRITE|GENERIC_ALL|FILE_WRITE_DATA|FILE_APPEND_DATA|FILE_WRITE_EA|FILE_WRITE_ATTRIBUTES|DELETE|WRITE_DAC|WRITE_OWNER)) || disposition!=OPEN_EXISTING || (flags&FILE_FLAG_DELETE_ON_CLOSE)) { SetLastError(ERROR_ACCESS_DENIED); return INVALID_HANDLE_VALUE; }
                if (files.contains(rel)) return open_virtual(rel,access,share,sa,disposition,flags);
            }
        }
    } catch (...) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return INVALID_HANDLE_VALUE; }
    return real_CreateFileA(name,access,share,sa,disposition,flags,templ);
}
BOOL WINAPI hook_ReadFile(HANDLE handle,LPVOID buffer,DWORD count,LPDWORD received,LPOVERLAPPED overlapped) noexcept {
    if (inside()) return real_ReadFile(handle,buffer,count,received,overlapped);
    Guard guard; std::lock_guard lock(mutex); auto it=streams.find(handle);
    if (it==streams.end()) return real_ReadFile(handle,buffer,count,received,overlapped);
    if (received) *received=0;
    if (overlapped) { SetLastError(ERROR_NOT_SUPPORTED); return FALSE; }
    if (!received || (!buffer&&count)) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (!(it->second.access&(GENERIC_READ|FILE_READ_DATA))) { SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    auto& stream=it->second;
    const size_t available=stream.position<stream.data->size()?stream.data->size()-static_cast<size_t>(stream.position):0;
    const size_t size=std::min<size_t>(available,count);
    if (size) std::memcpy(buffer,stream.data->data()+static_cast<size_t>(stream.position),size);
    stream.position+=size; *received=static_cast<DWORD>(size); return TRUE;
}
BOOL WINAPI hook_WriteFile(HANDLE handle,LPCVOID buffer,DWORD count,LPDWORD written,LPOVERLAPPED overlapped) noexcept {
    if (inside()) return real_WriteFile(handle,buffer,count,written,overlapped);
    Guard guard; std::lock_guard lock(mutex);
    if (streams.contains(handle)) { if (written) *written=0; SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    return real_WriteFile(handle,buffer,count,written,overlapped);
}
BOOL WINAPI hook_CloseHandle(HANDLE handle) noexcept {
    if (inside()) return real_CloseHandle(handle);
    Guard guard; std::lock_guard lock(mutex);
    if (streams.erase(handle)) return real_CloseHandle(handle);
    if (searches.contains(handle)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    return real_CloseHandle(handle);
}
BOOL WINAPI hook_DuplicateHandle(HANDLE source_process,HANDLE source,HANDLE target_process,LPHANDLE target,DWORD access,BOOL inherit,DWORD options) noexcept {
    if (inside()) return real_DuplicateHandle(source_process,source,target_process,target,access,inherit,options);
    Guard guard; std::lock_guard lock(mutex);
    if (GetProcessId(source_process)==GetCurrentProcessId() && (streams.contains(source)||searches.contains(source))) {
        if (target) *target=nullptr;
        // Never let DUPLICATE_CLOSE_SOURCE retire an OS token behind the registry.
        SetLastError(ERROR_NOT_SUPPORTED); return FALSE;
    }
    return real_DuplicateHandle(source_process,source,target_process,target,access,inherit,options);
}
BOOL seek(Stream& stream,LARGE_INTEGER distance,PLARGE_INTEGER result,DWORD method) noexcept {
    uint64_t base;
    if (method==FILE_BEGIN) base=0;
    else if (method==FILE_CURRENT) base=stream.position;
    else if (method==FILE_END) base=stream.data->size();
    else { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (distance.QuadPart<0) {
        const uint64_t amount=static_cast<uint64_t>(-(distance.QuadPart+1))+1;
        if (amount>base) { SetLastError(ERROR_NEGATIVE_SEEK); return FALSE; }
        base-=amount;
    } else {
        if (static_cast<uint64_t>(distance.QuadPart)>INT64_MAX-base) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
        base+=static_cast<uint64_t>(distance.QuadPart);
    }
    stream.position=base; if (result) result->QuadPart=static_cast<LONGLONG>(base); return TRUE;
}
BOOL WINAPI hook_SetFilePointerEx(HANDLE handle,LARGE_INTEGER distance,PLARGE_INTEGER result,DWORD method) noexcept {
    if (inside()) return real_SetFilePointerEx(handle,distance,result,method);
    Guard guard; std::lock_guard lock(mutex); auto it=streams.find(handle);
    return it==streams.end()?real_SetFilePointerEx(handle,distance,result,method):seek(it->second,distance,result,method);
}
DWORD WINAPI hook_SetFilePointer(HANDLE handle,LONG low,PLONG high,DWORD method) noexcept {
    if (inside()) return real_SetFilePointer(handle,low,high,method);
    Guard guard; std::lock_guard lock(mutex); auto it=streams.find(handle);
    if (it==streams.end()) return real_SetFilePointer(handle,low,high,method);
    LARGE_INTEGER distance,result;
    if (high) { distance.LowPart=static_cast<DWORD>(low); distance.HighPart=*high; } else distance.QuadPart=low;
    if (!seek(it->second,distance,&result,method)) return INVALID_SET_FILE_POINTER;
    if (high) *high=result.HighPart;
    SetLastError(NO_ERROR); return result.LowPart;
}
BOOL WINAPI hook_GetFileSizeEx(HANDLE handle,PLARGE_INTEGER size) noexcept {
    if (inside()) return real_GetFileSizeEx(handle,size);
    Guard guard; std::lock_guard lock(mutex); auto it=streams.find(handle);
    if (it==streams.end()) return real_GetFileSizeEx(handle,size);
    if (!size) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    size->QuadPart=static_cast<LONGLONG>(it->second.data->size()); return TRUE;
}
DWORD WINAPI hook_GetFileSize(HANDLE handle,LPDWORD high) noexcept {
    if (inside()) return real_GetFileSize(handle,high);
    Guard guard; std::lock_guard lock(mutex); auto it=streams.find(handle);
    if (it==streams.end()) return real_GetFileSize(handle,high);
    const uint64_t size=it->second.data->size(); if (high) *high=static_cast<DWORD>(size>>32);
    SetLastError(NO_ERROR); return static_cast<DWORD>(size);
}
BOOL WINAPI hook_FlushFileBuffers(HANDLE handle) noexcept {
    if (inside()) return real_FlushFileBuffers(handle);
    Guard guard; std::lock_guard lock(mutex);
    if (streams.contains(handle)) { SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    return real_FlushFileBuffers(handle);
}
HANDLE WINAPI hook_CreateFileMappingW(HANDLE handle,LPSECURITY_ATTRIBUTES attrs,DWORD protect,DWORD high,DWORD low,LPCWSTR name) noexcept {
    if (inside()) return real_CreateFileMappingW(handle,attrs,protect,high,low,name);
    Guard guard; std::lock_guard lock(mutex);
    if (streams.contains(handle)) { SetLastError(ERROR_NOT_SUPPORTED); return nullptr; }
    return real_CreateFileMappingW(handle,attrs,protect,high,low,name);
}
DWORD WINAPI hook_GetFileAttributesW(LPCWSTR name) noexcept {
    if (inside() || !eligible(_ReturnAddress())) return real_GetFileAttributesW(name);
    Guard guard;
    try { std::lock_guard lock(mutex); std::wstring rel;
        if (!initialized || !hooks_enabled) return real_GetFileAttributesW(name);
        if (under_root(name,rel)) {
            if (files.contains(rel)) return FILE_ATTRIBUTE_READONLY|FILE_ATTRIBUTE_ARCHIVE;
            if (virtual_directory(rel)) return FILE_ATTRIBUTE_READONLY|FILE_ATTRIBUTE_DIRECTORY;
        }
    } catch (...) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return INVALID_FILE_ATTRIBUTES; }
    return real_GetFileAttributesW(name);
}

HANDLE WINAPI hook_FindFirstFileW(LPCWSTR pattern,LPWIN32_FIND_DATAW output) noexcept {
    if (inside() || !eligible(_ReturnAddress())) return real_FindFirstFileW(pattern,output);
    Guard guard;
    try { std::lock_guard lock(mutex);
        if (!initialized || !hooks_enabled) return real_FindFirstFileW(pattern,output);
        auto full=canonical(pattern);
        const auto split=full.find_last_of(L'\\');
        if (split==std::wstring::npos) return real_FindFirstFileW(pattern,output);
        std::wstring dir=full.substr(0,split+1), rel;
        if (dir.size()<root.size() || !equal(dir.substr(0,root.size()),root)) return real_FindFirstFileW(pattern,output);
        rel=dir.substr(root.size()); const auto wildcard=full.substr(split+1);
        const bool all=wildcard==L"*" || wildcard==L"*.*";
        if (!all && wildcard.find_first_of(L"*?")!=std::wstring::npos) { SetLastError(ERROR_NOT_SUPPORTED); return INVALID_HANDLE_VALUE; }
        if (!output) { SetLastError(ERROR_INVALID_PARAMETER); return INVALID_HANDLE_VALUE; }
        std::map<std::wstring,WIN32_FIND_DATAW,ComparePath> entries;
        WIN32_FIND_DATAW data{}; HANDLE original=real_FindFirstFileW(pattern,&data);
        if (original!=INVALID_HANDLE_VALUE) {
            HandleScope scope{original,real_FindClose};
            do { entries[data.cFileName]=data; } while (real_FindNextFileW(original,&data));
            if (GetLastError()!=ERROR_NO_MORE_FILES) return INVALID_HANDLE_VALUE;
        } else if (GetLastError()!=ERROR_FILE_NOT_FOUND && GetLastError()!=ERROR_PATH_NOT_FOUND) return INVALID_HANDLE_VALUE;
        for (const auto& [path,file]:files) {
            if (path.size()<=rel.size() || !equal(path.substr(0,rel.size()),rel)) continue;
            auto rest=path.substr(rel.size()); const auto slash=rest.find(L'\\'); const auto name=rest.substr(0,slash);
            if (!all && !equal(name,wildcard)) continue;
            if (name.size()>=MAX_PATH) { SetLastError(ERROR_FILENAME_EXCED_RANGE); return INVALID_HANDLE_VALUE; }
            WIN32_FIND_DATAW item{};
            item.dwFileAttributes=FILE_ATTRIBUTE_READONLY|(slash==std::wstring::npos?FILE_ATTRIBUTE_ARCHIVE:FILE_ATTRIBUTE_DIRECTORY);
            if (slash==std::wstring::npos) { const auto size=static_cast<uint64_t>(file.data->size()); item.nFileSizeLow=static_cast<DWORD>(size); item.nFileSizeHigh=static_cast<DWORD>(size>>32); }
            std::wmemcpy(item.cFileName,name.c_str(),name.size()+1); entries[name]=item;
        }
        if (entries.empty()) { SetLastError(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE_VALUE; }
        Search search; for (const auto& [name,entry]:entries) { (void)name; search.entries.push_back(entry); }
        HANDLE handle=CreateEventW(nullptr,TRUE,FALSE,nullptr);
        if (!handle) return INVALID_HANDLE_VALUE;
        *output=search.entries[0]; search.next=1;
        try { searches.emplace(handle,std::move(search)); } catch (...) { real_CloseHandle(handle); throw; }
        return handle;
    } catch (...) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return INVALID_HANDLE_VALUE; }
}
BOOL WINAPI hook_FindNextFileW(HANDLE handle,LPWIN32_FIND_DATAW output) noexcept {
    if (inside()) return real_FindNextFileW(handle,output);
    Guard guard; std::lock_guard lock(mutex); auto it=searches.find(handle);
    if (it==searches.end()) return real_FindNextFileW(handle,output);
    if (!output) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (it->second.next==it->second.entries.size()) { SetLastError(ERROR_NO_MORE_FILES); return FALSE; }
    *output=it->second.entries[it->second.next++]; return TRUE;
}
BOOL WINAPI hook_FindClose(HANDLE handle) noexcept {
    if (inside()) return real_FindClose(handle);
    Guard guard; std::lock_guard lock(mutex);
    if (searches.erase(handle)) return real_CloseHandle(handle);
    if (streams.contains(handle)) { SetLastError(ERROR_INVALID_HANDLE); return FALSE; }
    return real_FindClose(handle);
}

LPVOID WINAPI hook_HeapAlloc(HANDLE heap,DWORD flags,SIZE_T size) noexcept {
    if (inside() || !eligible(_ReturnAddress()) || heap!=selected_heap || (flags&~HEAP_ZERO_MEMORY)) return real_HeapAlloc(heap,flags,size);
    Guard guard;
    try { std::lock_guard lock(mutex);
        if (!initialized || !hooks_enabled) return real_HeapAlloc(heap,flags,size);
        void* p=allocate(size,16,Owner::heap,heap); if (p && flags&HEAP_ZERO_MEMORY) std::memset(p,0,size); return p; }
    catch (...) { return nullptr; }
}
BOOL WINAPI hook_HeapFree(HANDLE heap,DWORD flags,LPVOID pointer) noexcept {
    if (inside()) return real_HeapFree(heap,flags,pointer);
    Guard guard; std::lock_guard lock(mutex); auto it=allocations.find(pointer);
    if (it==allocations.end()) return real_HeapFree(heap,flags,pointer);
    if (it->second.owner!=Owner::heap || it->second.heap!=heap || flags&~HEAP_NO_SERIALIZE) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    release(it); return TRUE;
}
LPVOID WINAPI hook_HeapReAlloc(HANDLE heap,DWORD flags,LPVOID pointer,SIZE_T size) noexcept {
    if (inside()) return real_HeapReAlloc(heap,flags,pointer,size);
    Guard guard;
    try { std::lock_guard lock(mutex); auto it=allocations.find(pointer);
        if (it==allocations.end()) return real_HeapReAlloc(heap,flags,pointer,size);
        if (it->second.owner!=Owner::heap || it->second.heap!=heap || flags&~HEAP_ZERO_MEMORY) { SetLastError(ERROR_NOT_SUPPORTED); return nullptr; }
        return resize_owned(it,size,(flags&HEAP_ZERO_MEMORY)!=0);
    } catch (...) { return nullptr; }
}
SIZE_T WINAPI hook_HeapSize(HANDLE heap,DWORD flags,LPCVOID pointer) noexcept {
    if (inside()) return real_HeapSize(heap,flags,pointer);
    Guard guard; std::lock_guard lock(mutex); auto it=allocations.find(const_cast<void*>(pointer));
    if (it==allocations.end()) return real_HeapSize(heap,flags,pointer);
    if (it->second.owner!=Owner::heap || it->second.heap!=heap || flags&~HEAP_NO_SERIALIZE) { SetLastError(ERROR_INVALID_PARAMETER); return static_cast<SIZE_T>(-1); }
    return it->second.bytes;
}
BOOL WINAPI hook_HeapDestroy(HANDLE heap) noexcept {
    if (inside()) return real_HeapDestroy(heap);
    Guard guard; std::lock_guard lock(mutex);
    for (const auto& [pointer,allocation]:allocations) { (void)pointer;
        if (allocation.owner==Owner::heap && allocation.heap==heap) { SetLastError(ERROR_BUSY); return FALSE; }
    }
    return real_HeapDestroy(heap);
}
LPVOID WINAPI hook_VirtualAlloc(LPVOID address,SIZE_T size,DWORD kind,DWORD protect) noexcept {
    if (!inside() && eligible(_ReturnAddress())) { Guard guard; std::lock_guard lock(mutex); ++virtual_alloc_calls; }
    // Reservation, commit, decommit and page-protection semantics are not malloc semantics.
    return real_VirtualAlloc(address,size,kind,protect);
}
BOOL WINAPI hook_VirtualFree(LPVOID address,SIZE_T size,DWORD kind) noexcept { return real_VirtualFree(address,size,kind); }
void* __cdecl hook_malloc(size_t size) noexcept {
    if (inside() || !eligible(_ReturnAddress())) return real_malloc(size);
    Guard guard; try { std::lock_guard lock(mutex);
        if (!initialized || !hooks_enabled) return real_malloc(size);
        return allocate(size,16,Owner::crt,nullptr); } catch (...) { return nullptr; }
}
void __cdecl hook_free(void* pointer) noexcept {
    if (inside()) { real_free(pointer); return; }
    Guard guard; std::lock_guard lock(mutex); auto it=allocations.find(pointer);
    if (it==allocations.end()) { real_free(pointer); return; }
    if (it->second.owner==Owner::crt) release(it); else log("refused cross-family CRT free");
}
void* __cdecl hook_realloc(void* pointer,size_t size) noexcept {
    if (inside()) return real_realloc(pointer,size);
    Guard guard;
    try { std::lock_guard lock(mutex); auto it=allocations.find(pointer);
        if (it==allocations.end()) return real_realloc(pointer,size);
        if (it->second.owner!=Owner::crt) return nullptr;
        if (!size) { release(it); return nullptr; }
        return resize_owned(it,size,false);
    } catch (...) { return nullptr; }
}
size_t __cdecl hook_msize(void* pointer) noexcept {
    if (inside()) return real_msize(pointer);
    Guard guard; std::lock_guard lock(mutex); auto it=allocations.find(pointer);
    if (it==allocations.end()) return real_msize(pointer);
    return it->second.owner==Owner::crt?it->second.bytes:static_cast<size_t>(-1);
}

} // namespace

RS_API int __cdecl RiftstoneRuntime_Initialize(const RsConfig* config) noexcept {
    Guard guard;
    try { std::lock_guard lock(mutex);
        if (initialized || hooks_created) return RS_BUSY;
        if (!config || config->size!=sizeof(RsConfig) || config->version!=1 || !config->base_root || !config->allocation_limit ||
            !config->pointer_ceiling || (config->use_mimalloc!=0 && config->use_mimalloc!=1)) return RS_BAD_ARGUMENT;
        auto new_root=canonical(config->base_root);
        if (new_root.empty() || real_GetFileAttributesW(new_root.c_str())==INVALID_FILE_ATTRIBUTES ||
            !(real_GetFileAttributesW(new_root.c_str())&FILE_ATTRIBUTE_DIRECTORY)) return RS_BAD_ARGUMENT;
        if (!external_log(config->log_path,new_root)) return RS_BAD_ARGUMENT;
        if (reentry_slot==TLS_OUT_OF_INDEXES) {
            const DWORD slot=TlsAlloc();
            if (slot==TLS_OUT_OF_INDEXES) return RS_NO_MEMORY;
            if (slot>=TLS_MINIMUM_AVAILABLE) { TlsFree(slot); return RS_UNAVAILABLE; }
            reentry_slot=slot;
            Guard::set(true);
        }
        if (new_root.back()!=L'\\') new_root+=L'\\';
#ifndef RS_HAVE_MIMALLOC
        if (config->use_mimalloc) return RS_UNAVAILABLE;
#else
        if (config->use_mimalloc) {
            // A private, exclusive arena; never claim to add address bits. Arena
            // metadata/reservation is retained for this pinned module's process lifetime.
            if (!mi_arena_ready) {
                if (mi_reserve_os_memory_ex(64u*1024u*1024u,false,false,true,&mi_arena)!=0) return RS_NO_MEMORY;
                mi_arena_ready=true;
            }
            if (!(mi_heap=mi_heap_new_in_arena(mi_arena))) return RS_NO_MEMORY;
        }
#endif
        private_heap=HeapCreate(0,0,0);
        if (!private_heap) {
#ifdef RS_HAVE_MIMALLOC
            if (mi_heap) { mi_heap_delete(mi_heap); mi_heap=nullptr; }
#endif
            return RS_NO_MEMORY;
        }
        root=std::move(new_root); allocation_limit=config->allocation_limit; pointer_ceiling=config->pointer_ceiling;
        selected_heap=config->redirect_heap; use_mimalloc=config->use_mimalloc!=0; owner_thread=GetCurrentThreadId();
        if (config->log_path && *config->log_path) {
            log_file=real_CreateFileW(config->log_path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
            if (log_file==INVALID_HANDLE_VALUE) { real_HeapDestroy(private_heap); private_heap=nullptr;
#ifdef RS_HAVE_MIMALLOC
                if (mi_heap) { mi_heap_delete(mi_heap); mi_heap=nullptr; }
#endif
                return RS_BAD_ARGUMENT;
            }
        }
        initialized=true; frozen=false;
        log("initialized explicit runtime; x86 address ceiling is unchanged; no internal game profiles enabled"); return RS_OK;
    } catch (...) { return RS_NO_MEMORY; }
}
RS_API void* __cdecl RiftstoneRuntime_Allocate(size_t size,size_t alignment) noexcept {
    Guard guard; try { std::lock_guard lock(mutex); return allocate(size,alignment,Owner::direct,nullptr); } catch (...) { return nullptr; }
}
RS_API int __cdecl RiftstoneRuntime_Free(void* pointer) noexcept {
    Guard guard; std::lock_guard lock(mutex);
    if (!initialized) return RS_NOT_INITIALIZED;
    if (!pointer) return RS_OK;
    auto it=allocations.find(pointer);
    if (it==allocations.end() || it->second.owner!=Owner::direct) return RS_NOT_OWNED;
    release(it); return RS_OK;
}
RS_API int __cdecl RiftstoneRuntime_AddFile(const wchar_t* name,const void* bytes,size_t size,int priority) noexcept {
    Guard guard;
    try { std::lock_guard lock(mutex);
        if (!initialized) return RS_NOT_INITIALIZED;
        if (frozen) return RS_BUSY;
        if (!name || !relative_valid(name) || (!bytes&&size) || size>256u*1024u*1024u) return RS_BAD_ARGUMENT;
        std::wstring path(name); std::replace(path.begin(),path.end(),L'/',L'\\');
        if (!base_compatible_file(path)) return RS_BAD_ARGUMENT;
        for (const auto& [existing,file]:files) { (void)file;
            if ((existing.size()>path.size() && existing[path.size()]==L'\\' && equal(existing.substr(0,path.size()),path)) ||
                (path.size()>existing.size() && path[existing.size()]==L'\\' && equal(path.substr(0,existing.size()),existing))) return RS_BAD_ARGUMENT;
        }
        auto current=files.find(path);
        if (current!=files.end() && priority<=current->second.priority) return priority==current->second.priority?RS_BAD_ARGUMENT:RS_OK;
        auto data=std::make_shared<Bytes>(size);
        if (size) std::memcpy(data->data(),bytes,size);
        files.insert_or_assign(path,File{data,priority}); return RS_OK;
    } catch (...) { return RS_NO_MEMORY; }
}
RS_API int __cdecl RiftstoneRuntime_Stats(RsStats* output) noexcept {
    Guard guard; std::lock_guard lock(mutex);
    if (!output || output->size!=sizeof(RsStats)) return RS_BAD_ARGUMENT;
    *output=RsStats{sizeof(RsStats),allocations.size(),owned_bytes,streams.size()+searches.size(),redirects,virtual_alloc_calls}; return RS_OK;
}
RS_API int __cdecl RiftstoneRuntime_LoadBundle(const void* raw,size_t size) noexcept {
    Guard guard;
    try { std::lock_guard lock(mutex);
        if (!initialized) return RS_NOT_INITIALIZED;
        if (frozen || !streams.empty() || !searches.empty()) return RS_BUSY;
        if (!raw || size<8 || size>1024u*1024u*1024u || std::memcmp(raw,"RSV1",4)) return RS_BAD_ARGUMENT;
        const auto* bytes=static_cast<const unsigned char*>(raw);
        size_t at=4;
        auto number=[&](uint32_t& value) { if (at>size || size-at<4) return false; std::memcpy(&value,bytes+at,4); at+=4; return true; };
        uint32_t count; if (!number(count) || count>100000) return RS_BAD_ARGUMENT;
        std::map<std::wstring,File,ComparePath> staged;
        for (uint32_t i=0;i<count;++i) {
            uint32_t name_size,data_size;
            if (!number(name_size)||!number(data_size)||!name_size||name_size>120000||data_size>256u*1024u*1024u||at>size||name_size>size-at) return RS_BAD_ARGUMENT;
            const char* utf8=reinterpret_cast<const char*>(bytes+at);
            int characters=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,utf8,static_cast<int>(name_size),nullptr,0);
            if (characters<=0 || std::memchr(utf8,0,name_size)) return RS_BAD_ARGUMENT;
            std::wstring name(static_cast<size_t>(characters),L'\0');
            MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,utf8,static_cast<int>(name_size),name.data(),characters);
            at+=name_size;
            if (!relative_valid(name)||data_size>size-at) return RS_BAD_ARGUMENT;
            std::replace(name.begin(),name.end(),L'/',L'\\');
            if (!base_compatible_file(name)) return RS_BAD_ARGUMENT;
            if (staged.contains(name)) return RS_BAD_ARGUMENT;
            auto data=std::make_shared<Bytes>(bytes+at,bytes+at+data_size); at+=data_size;
            staged.emplace(std::move(name),File{data,0});
        }
        if (at!=size) return RS_BAD_ARGUMENT;
        for (const auto& [name,file]:staged) {
            (void)file;
            for (auto slash=name.find(L'\\');slash!=std::wstring::npos;slash=name.find(L'\\',slash+1))
                if (staged.contains(name.substr(0,slash))) return RS_BAD_ARGUMENT;
        }
        files.swap(staged); return RS_OK;
    } catch (...) { return RS_NO_MEMORY; }
}
RS_API int __cdecl RiftstoneRuntime_GameProfile(const char* feature) noexcept {
    if (!feature) return RS_BAD_ARGUMENT;
    // The measured enemy-cap provider is a separate existing plugin (10..64).
    // No allocator, Warrior/UI, icon-cache or 256-slot profile has been verified here.
    return RS_UNAVAILABLE;
}

#ifdef RS_HAVE_MINHOOK
namespace {
std::vector<void*> hook_targets;
struct Hook { const wchar_t* library; const char* name; void* callback; void** original; bool optional; };
#define RS_HOOK(name) {L"kernel32.dll",#name,reinterpret_cast<void*>(hook_##name),reinterpret_cast<void**>(&real_##name),false}
Hook definitions[]={
    RS_HOOK(CreateFileW),RS_HOOK(CreateFileA),RS_HOOK(ReadFile),RS_HOOK(CloseHandle),RS_HOOK(DuplicateHandle),RS_HOOK(GetFileAttributesW),
    RS_HOOK(FindFirstFileW),RS_HOOK(FindNextFileW),RS_HOOK(FindClose),RS_HOOK(SetFilePointerEx),RS_HOOK(SetFilePointer),
    RS_HOOK(GetFileSizeEx),RS_HOOK(GetFileSize),RS_HOOK(WriteFile),RS_HOOK(FlushFileBuffers),RS_HOOK(CreateFileMappingW),
    RS_HOOK(HeapAlloc),RS_HOOK(HeapFree),RS_HOOK(HeapReAlloc),RS_HOOK(HeapSize),RS_HOOK(HeapDestroy),
    RS_HOOK(VirtualAlloc),RS_HOOK(VirtualFree),
    {L"ucrtbase.dll","malloc",reinterpret_cast<void*>(hook_malloc),reinterpret_cast<void**>(&real_malloc),true},
    {L"ucrtbase.dll","free",reinterpret_cast<void*>(hook_free),reinterpret_cast<void**>(&real_free),true},
    {L"ucrtbase.dll","realloc",reinterpret_cast<void*>(hook_realloc),reinterpret_cast<void**>(&real_realloc),true},
    {L"ucrtbase.dll","_msize",reinterpret_cast<void*>(hook_msize),reinterpret_cast<void**>(&real_msize),true},
};
#undef RS_HOOK
}
#endif
RS_API int __cdecl RiftstoneRuntime_EnableHooks(HMODULE caller) noexcept {
    Guard guard;
#ifndef RS_HAVE_MINHOOK
    (void)caller; return RS_UNAVAILABLE;
#else
    try { std::lock_guard lock(mutex);
        if (!initialized) return RS_NOT_INITIALIZED;
        if (hooks_created || hooks_enabled || !allocations.empty()) return RS_BUSY;
        // Only the current executable is accepted. It is a deliberate local test host,
        // never an arbitrary DLL or a remote process selected by path/address.
        if (!caller || caller!=GetModuleHandleW(nullptr)) return RS_BAD_ARGUMENT;
        auto* dos=reinterpret_cast<IMAGE_DOS_HEADER*>(caller);
        if (dos->e_magic!=IMAGE_DOS_SIGNATURE) return RS_BAD_ARGUMENT;
        auto* nt=reinterpret_cast<IMAGE_NT_HEADERS*>(reinterpret_cast<unsigned char*>(caller)+dos->e_lfanew);
        if (nt->Signature!=IMAGE_NT_SIGNATURE || nt->FileHeader.Machine!=IMAGE_FILE_MACHINE_I386) return RS_BAD_ARGUMENT;
        caller_begin=reinterpret_cast<uintptr_t>(caller); caller_end=caller_begin+nt->OptionalHeader.SizeOfImage;
        if (caller_end<caller_begin) return RS_BAD_ARGUMENT;
        hook_targets.reserve(std::size(definitions));
        if (MH_Initialize()!=MH_OK) return RS_HOOK_FAILED;
        auto rollback=[]() {
            for (void* target:hook_targets) { MH_DisableHook(target); MH_RemoveHook(target); }
            for (auto& hook:definitions) {
                HMODULE module=GetModuleHandleW(hook.library);
                *hook.original=module?reinterpret_cast<void*>(GetProcAddress(module,hook.name)):nullptr;
            }
            hook_targets.clear(); MH_Uninitialize(); hooks_enabled=false;
        };
        for (auto& hook:definitions) {
            HMODULE module=GetModuleHandleW(hook.library);
            if (!module && hook.optional) continue;
            void* target=module?reinterpret_cast<void*>(GetProcAddress(module,hook.name)):nullptr;
            if (!target || MH_CreateHook(target,hook.callback,hook.original)!=MH_OK) { rollback(); return RS_HOOK_FAILED; }
            hook_targets.push_back(target);
            if (MH_QueueEnableHook(target)!=MH_OK) { rollback(); return RS_HOOK_FAILED; }
        }
        HMODULE pinned;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
                               reinterpret_cast<LPCWSTR>(&RiftstoneRuntime_EnableHooks),&pinned)) { rollback(); return RS_HOOK_FAILED; }
        hooks_enabled=true; frozen=true;
        if (MH_ApplyQueued()!=MH_OK) { rollback(); frozen=false; return RS_HOOK_FAILED; }
        hooks_created=true;
        log("MinHook adapters enabled for direct calls from the explicit host; virtual APIs preserve reservation semantics");
        return RS_OK;
    } catch (...) { return RS_NO_MEMORY; }
#endif
}
RS_API int __cdecl RiftstoneRuntime_DisableHooks() noexcept {
    Guard guard; std::lock_guard lock(mutex);
    if (!hooks_enabled) return RS_OK;
    if (!streams.empty() || !searches.empty() || !allocations.empty()) return RS_BUSY;
#ifdef RS_HAVE_MINHOOK
    for (void* target:hook_targets) if (MH_QueueDisableHook(target)!=MH_OK) return RS_HOOK_FAILED;
    if (MH_ApplyQueued()!=MH_OK) return RS_HOOK_FAILED;
#endif
    hooks_enabled=false;
    // Keep trampolines and pinned code alive. A thread already entering a hook
    // cannot ever jump through a freed trampoline during teardown.
    log("hooks disabled; pinned code and trampolines retained until process exit"); return RS_OK;
}
RS_API int __cdecl RiftstoneRuntime_Shutdown() noexcept {
    Guard guard; std::lock_guard lock(mutex);
    if (!initialized) return RS_NOT_INITIALIZED;
    if (hooks_enabled || !streams.empty() || !searches.empty() || !allocations.empty()) return RS_BUSY;
    if (use_mimalloc && GetCurrentThreadId()!=owner_thread) return RS_WRONG_THREAD;
    files.clear();
#ifdef RS_HAVE_MIMALLOC
    if (mi_heap) { mi_heap_delete(mi_heap); mi_heap=nullptr; }
#endif
    real_HeapDestroy(private_heap); private_heap=nullptr;
    log("shutdown complete; no base files changed");
    if (log_file!=INVALID_HANDLE_VALUE) { real_CloseHandle(log_file); log_file=INVALID_HANDLE_VALUE; }
    initialized=false; return RS_OK;
}
BOOL APIENTRY DllMain(HMODULE module,DWORD reason,LPVOID) {
    if (reason==DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(module);
    return TRUE;
}
