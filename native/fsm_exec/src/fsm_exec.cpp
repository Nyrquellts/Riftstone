// fsm_exec_core -- runs Dark Arisen's own state-machine code on made-up machines, without the game.
//
//   fsm_stub.exe <DDDA.exe> <cases.txt>
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, as the plugin
// harnesses do (nothing is patched, the game is not launched), then for every case in cases.txt
// builds the runtime objects the code reads (fsm_layout.h: the engine's own sizes, the offsets its
// routines use) and calls it:
//   T  cAIFSM::Core's transition check (0x00E06710) on one ClusterDriveInfo: the states entered from
//      any state, then the current state's links (0x00E05800), conditions looked up by id
//      (0x0117AAA0) and their results read (0x01179B70, 0x01179C90).  A condition's root is a stand-in
//      whose result is the case's truth value, so the transition logic alone is exercised.
//   Q  a condition made of the game's own cAIConditionTree::OperationWorkNode (vtable 0x01444D90)
//      over ConstWorkNode operands (0x01444DD0) that hold rAIConditionTree::ConstS32Node /
//      ConstF32Node resources (0x014486E8 / 0x01448788), looked up and read through the same two
//      routines: every operator and operand count on constants.
// The case file comes from native/fsm_exec/test/run_tests.py, which asks riftstone's fsmcheck.py what
// each case should give and compares.  Output, one line per case:
//   R <case> <returned> <flag +0x14> <flag +0x15> <entered state index or -1>
//   V <case> <found> <holds>
//   X <case> <exception code>        (the call faulted; reported, never skipped)
// Exit 0 when every case ran, 1 on a bad case file, 2 when the image cannot be mapped here or is not
// build 2364871 (run_tests.py reports a skip).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "fsm_layout.h"

namespace {
constexpr uintptr_t BASE = 0x00400000;
// DDDA.exe build 2364871: the routines, and the bytes each starts with (checked before any call).
constexpr uintptr_t TRANSITION_CHECK = 0x00E06710;  // cAIFSM::Core, thiscall(ClusterDriveInfo*) -> bool
constexpr uintptr_t LINK_WALK = 0x00E05800;         // cAIFSM::Core, thiscall(cluster, node) -> node or 0
constexpr uintptr_t CONDITION_SELECT = 0x0117AAA0;  // cAIConditionTree, thiscall(u32 id) -> found
constexpr uintptr_t CONDITION_TRUTH = 0x01179C90;   // cAIConditionTree, thiscall() -> holds
constexpr uintptr_t OPERATION_EVAL = 0x0117C040;    // OperationWorkNode's slot 6
constexpr uintptr_t VT_OPERATION_WORK = 0x01444D90, VT_CONST_WORK = 0x01444DD0;
constexpr uintptr_t VT_CONST_S32 = 0x014486E8, VT_CONST_F32 = 0x01448788;
struct Expect {
    uintptr_t at;
    const char* bytes;
} const EXPECT[] = {
    {TRANSITION_CHECK, "\x51\x53\x55\x56\x8b\x74\x24\x14\x33\xdb\x8b\xe9"},
    {LINK_WALK, "\x51\x83\x7c\x24\x08\x00\x55\x89\x4c\x24\x04\x74"},
    {CONDITION_SELECT, "\x53\x56\x8b\x71\x54\x33\xc0\x57\x85\xf6\x74\x25"},
    {CONDITION_TRUTH, "\x8b\x41\x08\x48\x83\xf8\x06\x77\x26\xff\x24\x85"},
    {OPERATION_EVAL, "\x8b\x44\x24\x04\x56\x50\x8b\xf1\xe8\xa3\xda\xff"},
};

// Everything a case builds lives here, cleared per case.
struct Arena {
    uint8_t* base = nullptr;
    size_t used = 0, size = 0;
    void* Take(size_t n) {
        n = (n + 15) & ~(size_t)15;
        if (used + n > size) {
            printf("the case needs more than %u bytes\n", (unsigned)size);
            ExitProcess(1);
        }
        void* p = base + used;
        used += n;
        return p;
    }
    template <class T>
    T* New(long long count = 1) {
        return (T*)Take(sizeof(T) * (size_t)(count > 0 ? count : 1));
    }
    void Reset() {
        memset(base, 0, used);
        used = 0;
    }
} g_arena;

// ---- a condition root whose result is given (T cases) ------------------------------------------
// The lookup (0x01179B70) calls slot 6 (evaluate, one argument), slot 7 (result type) and, for type
// 1 (bool), slot 8 (the value).  __fastcall with an unused edx is __thiscall for these.
struct StandIn {
    void** vftable;
    uint8_t value;
};
char __fastcall StandInEvaluate(void*, void*, void*) { return 1; }
int __fastcall StandInType(void*, void*) { return 1; }
char __fastcall StandInValue(void* self, void*) { return ((StandIn*)self)->value; }
void* g_standInVt[16];

// ---- reading the case file ----------------------------------------------------------------------
struct Reader {
    std::vector<std::string> tok;
    size_t at = 0;
    bool More() const { return at < tok.size(); }
    const std::string& Word() {
        if (at >= tok.size()) {
            printf("the case file ends inside a case\n");
            ExitProcess(1);
        }
        return tok[at++];
    }
    long long Num() {
        const std::string& w = Word();
        char* end = nullptr;
        long long v = _strtoi64(w.c_str(), &end, 0);
        if (!end || *end) {
            printf("not a number in the case file: %s\n", w.c_str());
            ExitProcess(1);
        }
        return v;
    }
    void Expect(const char* w) {
        if (Word() != w) {
            printf("the case file has %s where %s belongs\n", tok[at - 1].c_str(), w);
            ExitProcess(1);
        }
    }
};

// ---- T: one transition check ------------------------------------------------------------------
typedef char(__fastcall* CheckFn)(cAIFSMCore* core, void*, ClusterDriveInfo* drive);

int g_fault;
char Guarded(CheckFn fn, cAIFSMCore* core, ClusterDriveInfo* drive) {
    __try {
        return fn(core, nullptr, drive);
    } __except (g_fault = GetExceptionCode(), EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

void RunTransition(Reader& r, int number) {
    const uint32_t attribute = (uint32_t)r.Num();
    const uint32_t timer = (uint32_t)r.Num();
    const uint8_t lock = (uint8_t)r.Num();
    const long long current = r.Num();
    r.Expect("N");
    const long long n = r.Num();
    cAIFSMNode* nodes = g_arena.New<cAIFSMNode>(n);
    for (long long i = 0; i < n; i++) {
        r.Expect("S");
        cAIFSMNode& node = nodes[i];
        node.mId = (uint32_t)r.Num();
        node.mUniqueId = (uint32_t)r.Num();
        node.mSetting = (uint32_t)r.Num();
        node.mExistConditionTrainsitionFromAll = (uint8_t)r.Num();
        node.mConditionTrainsitionFromAllId = (uint32_t)r.Num();
        const long long links = r.Num();
        cAIFSMLink* ls = g_arena.New<cAIFSMLink>(links);
        node.linkCount = (uint32_t)links;
        node.links = links ? ls : nullptr;
        for (long long k = 0; k < links; k++) {
            r.Expect("L");
            ls[k].mDestinationNodeId = (uint32_t)r.Num();
            ls[k].mExistCondition = (uint8_t)r.Num();
            ls[k].mConditionId = (uint32_t)r.Num();
        }
    }
    cAIFSMCluster* cluster = g_arena.New<cAIFSMCluster>();
    cluster->nodeCount = (uint32_t)n;
    cluster->nodes = n ? nodes : nullptr;

    r.Expect("C");
    const long long conditions = r.Num();
    TreeInfo* infos = g_arena.New<TreeInfo>(conditions);
    for (long long i = 0; i < conditions; i++) {
        r.Expect("c");
        StandIn* root = g_arena.New<StandIn>();
        root->vftable = g_standInVt;
        infos[i].id = (uint32_t)r.Num();
        root->value = (uint8_t)r.Num();
        infos[i].root = root;
    }
    cAIFSMCore* core = g_arena.New<cAIFSMCore>();
    core->conditions.infoCount = (uint32_t)conditions;
    core->conditions.infos = conditions ? infos : nullptr;
    core->mAttribute = attribute;

    r.Expect("O");
    const long long once = r.Num();
    OnceList* onceList = nullptr;
    if (once >= 0) {
        onceList = g_arena.New<OnceList>();
        onceList->uniqueIds = g_arena.New<uint32_t>(once);
        for (long long i = 0; i < once; i++) onceList->uniqueIds[i] = (uint32_t)r.Num();
        onceList->count = (uint32_t)once;
    }

    ClusterDriveInfo* drive = g_arena.New<ClusterDriveInfo>();
    drive->cluster = cluster;
    drive->once = onceList;
    drive->current = (current >= 0 && current < n) ? &nodes[current] : nullptr;
    drive->go = drive->byEntry = 0xAB;  // sentinels: what the check leaves alone stays 0xAB
    drive->lock = lock;
    drive->timerRunning = timer;

    const char ret = Guarded((CheckFn)TRANSITION_CHECK, core, drive);
    if (ret == -1 && g_fault) {
        printf("X %d 0x%08X\n", number, (unsigned)g_fault);
        g_fault = 0;
        return;
    }
    long long index = -1;
    if (drive->next) {
        const uintptr_t off = (uintptr_t)drive->next - (uintptr_t)nodes;
        index = (off % sizeof(cAIFSMNode) == 0 && off / sizeof(cAIFSMNode) < (uintptr_t)n)
                    ? (long long)(off / sizeof(cAIFSMNode))
                    : -2;
    }
    printf("R %d %d %d %d %lld\n", number, (int)(uint8_t)ret, (int)drive->go, (int)drive->byEntry, index);
}

// ---- Q: one condition of real operation and constant nodes -------------------------------------
void* BuildTerm(Reader& r, int depth) {
    if (depth > 16) {
        printf("a condition in the case file nests deeper than 16\n");
        ExitProcess(1);
    }
    const std::string kind = r.Word();
    if (kind == "P") {
        OperationNodeRes* res = g_arena.New<OperationNodeRes>();
        res->mOperator = (uint32_t)r.Num();
        const long long kids = r.Num();
        void** list = g_arena.New<void*>(kids);
        for (long long i = 0; i < kids; i++) list[i] = BuildTerm(r, depth + 1);
        OperationWorkNode* work = g_arena.New<OperationWorkNode>();
        work->vftable = (uint32_t)VT_OPERATION_WORK;
        work->res = res;
        work->count = (uint32_t)kids;
        work->children = kids ? list : nullptr;
        return work;
    }
    ConstWorkNode* work = g_arena.New<ConstWorkNode>();
    work->vftable = (uint32_t)VT_CONST_WORK;
    if (kind == "K") {
        ConstS32NodeRes* res = g_arena.New<ConstS32NodeRes>();
        res->vftable = (uint32_t)VT_CONST_S32;
        res->mValue = (int32_t)r.Num();
        work->res = res;
        return work;
    }
    if (kind == "F") {
        ConstF32NodeRes* res = g_arena.New<ConstF32NodeRes>();
        res->vftable = (uint32_t)VT_CONST_F32;
        const uint32_t bits = (uint32_t)r.Num();
        memcpy(&res->mValue, &bits, 4);
        work->res = res;
        return work;
    }
    printf("unknown term %s in the case file\n", kind.c_str());
    ExitProcess(1);
}

typedef char(__fastcall* SelectFn)(cAIConditionTree* tree, void*, uint32_t id);
typedef char(__fastcall* TruthFn)(cAIConditionTree* tree, void*);

int Evaluate(cAIConditionTree* tree, char& found, char& holds) {
    __try {
        found = ((SelectFn)CONDITION_SELECT)(tree, nullptr, 1);
        holds = ((TruthFn)CONDITION_TRUTH)(tree, nullptr);
        return 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return (int)GetExceptionCode();
    }
}

void RunCondition(Reader& r, int number) {
    void* root = BuildTerm(r, 0);
    TreeInfo* info = g_arena.New<TreeInfo>();
    info->id = 1;
    info->root = root;
    cAIConditionTree* tree = g_arena.New<cAIConditionTree>();
    tree->infoCount = 1;
    tree->infos = info;
    char found = 0, holds = 0;
    const int fault = Evaluate(tree, found, holds);
    if (fault)
        printf("X %d 0x%08X\n", number, (unsigned)fault);
    else
        printf("V %d %d %d\n", number, (int)(uint8_t)found, (int)(uint8_t)holds);
}

// ---- mapping DDDA.exe ---------------------------------------------------------------------------
bool MapImage(const wchar_t* exe) {
    HANDLE f = CreateFileW(exe, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return printf("SKIP: cannot open the exe\n"), false;
    DWORD size = GetFileSize(f, nullptr), got = 0;
    std::vector<uint8_t> file(size);
    ReadFile(f, file.data(), size, &got, nullptr);
    CloseHandle(f);
    if (got != size || size < 0x400) return printf("SKIP: cannot read the exe\n"), false;
    auto* dos = (IMAGE_DOS_HEADER*)file.data();
    auto* nt = (IMAGE_NT_HEADERS32*)(file.data() + dos->e_lfanew);
    if (nt->OptionalHeader.ImageBase != BASE) return printf("SKIP: unexpected image base\n"), false;
    auto* host = (const uint8_t*)GetModuleHandleW(nullptr);
    auto* hostNt = (const IMAGE_NT_HEADERS32*)(host + ((const IMAGE_DOS_HEADER*)host)->e_lfanew);
    DWORD need = nt->OptionalHeader.SizeOfImage;
    if ((uintptr_t)host != BASE || hostNt->OptionalHeader.SizeOfImage < need)
        return printf("SKIP: run me through fsm_stub.exe\n"), false;
    void* at = (void*)BASE;
    DWORD old;
    if (!VirtualProtect(at, need, PAGE_EXECUTE_READWRITE, &old)) return printf("SKIP: cannot unprotect the host image\n"), false;
    memset(at, 0, need);
    memcpy(at, file.data(), nt->OptionalHeader.SizeOfHeaders);
    auto* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        DWORD n = min(sec->SizeOfRawData, sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData);
        if (n && sec->PointerToRawData + n <= size)
            memcpy((uint8_t*)at + sec->VirtualAddress, file.data() + sec->PointerToRawData, n);
    }
    for (const Expect& e : EXPECT)
        if (memcmp((const void*)e.at, e.bytes, 12) != 0)
            return printf("SKIP: 0x%08X is not the code this harness was written for (another build?)\n",
                          (unsigned)e.at), false;
    const uint32_t* opVt = (const uint32_t*)VT_OPERATION_WORK;
    if (opVt[6] != OPERATION_EVAL || ((const uint32_t*)VT_CONST_WORK)[6] != 0x00FBC0A0 ||
        ((const uint32_t*)VT_CONST_S32)[7] != 0x01206F90 || ((const uint32_t*)VT_CONST_F32)[9] != 0x00572680)
        return printf("SKIP: the condition node vtables are not the ones this harness was written for\n"), false;
    return true;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by fsm_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 3) {
        printf("usage: fsm_stub <DDDA.exe> <cases.txt>\n");
        return 1;
    }
    // Read the cases before the image goes over the stub.
    HANDLE f = CreateFileW(argv[2], GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return printf("cannot open the case file\n"), 1;
    DWORD size = GetFileSize(f, nullptr), got = 0;
    std::string text(size, '\0');
    ReadFile(f, &text[0], size, &got, nullptr);
    CloseHandle(f);
    Reader r;
    for (size_t i = 0; i < text.size();) {
        while (i < text.size() && (unsigned char)text[i] <= ' ') i++;
        size_t j = i;
        while (j < text.size() && (unsigned char)text[j] > ' ') j++;
        if (j > i) r.tok.emplace_back(text, i, j - i);
        i = j;
    }
    if (!MapImage(argv[1])) return 2;
    for (int i = 0; i < 16; i++) g_standInVt[i] = nullptr;
    g_standInVt[6] = (void*)StandInEvaluate;
    g_standInVt[7] = (void*)StandInType;
    g_standInVt[8] = (void*)StandInValue;
    g_arena.size = 16 << 20;
    g_arena.base = (uint8_t*)VirtualAlloc(nullptr, g_arena.size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    int number = 0;
    while (r.More()) {
        const std::string kind = r.Word();
        if (kind == "T")
            RunTransition(r, number);
        else if (kind == "Q")
            RunCondition(r, number);
        else
            return printf("unknown case %s\n", kind.c_str()), 1;
        g_arena.Reset();
        number++;
    }
    printf("done %d\n", number);
    return 0;
}
