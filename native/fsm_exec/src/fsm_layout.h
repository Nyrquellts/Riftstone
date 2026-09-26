// fsm_layout.h -- Dark Arisen's state-machine runtime objects, as DDDA.exe (build 2364871) lays them out.
//
// Each size is the class's own MtDTI size (the engine's registry, read by `ddon re`); each field is
// where the game's code reads it (the routine is named beside it). The static_asserts hold both, so a
// wrong reading fails to compile, and fsm_exec runs the game's routines on these objects, so a wrong
// one fails the test. Only the fields those routines touch are named; the rest is padding.
#pragma once

#include <stddef.h>
#include <stdint.h>

static_assert(sizeof(void*) == 4, "DDDA.exe is a 32-bit program");

struct cAIFSMLink {                       // MtDTI size 0x10, vftable 0x0143FE24
    uint32_t vftable;
    uint32_t mDestinationNodeId;          // createProperty 0x010C9980; read at 0x00E05864
    uint8_t mExistCondition;              // read at 0x00E0582E: false = skipped
    uint8_t pad09[3];
    uint32_t mConditionId;                // read at 0x00E05834
};
static_assert(sizeof(cAIFSMLink) == 0x10, "MtDTI size");
static_assert(offsetof(cAIFSMLink, mExistCondition) == 0x08 && offsetof(cAIFSMLink, mConditionId) == 0x0C, "0x010C9980");

struct cAIFSMNode {                       // MtDTI size 0xAC, vftable 0x0143FED8
    uint32_t vftable;
    uint32_t mId;                         // createProperty 0x010CAA00
    uint32_t mUniqueId;
    uint32_t mOwnerId;
    uint32_t linkCount;                   // getLink 0x010C9200
    cAIFSMLink* links;
    uint32_t mpSubCluster;                // createProperty; entering starts it (0x00E093E2)
    uint32_t processCount;                // getProcess 0x010C9220 (0x68 bytes each)
    uint32_t processes;
    uint32_t mSetting;                    // bit 1: check after the actions; bit 8: an entry used once
    uint32_t mUserAttribute;
    uint8_t mExistConditionTrainsitionFromAll;   // read at 0x00E0675B (== 1)
    uint8_t pad2D[3];
    uint32_t mConditionTrainsitionFromAllId;     // read at 0x00E0677B
    uint8_t rest[0xAC - 0x34];
};
static_assert(sizeof(cAIFSMNode) == 0xAC, "MtDTI size, and getNode's stride (0x010C9270)");
static_assert(offsetof(cAIFSMNode, linkCount) == 0x10 && offsetof(cAIFSMNode, links) == 0x14, "0x010C9200");
static_assert(offsetof(cAIFSMNode, mpSubCluster) == 0x18 && offsetof(cAIFSMNode, mSetting) == 0x24, "0x010CAA00");
static_assert(offsetof(cAIFSMNode, mExistConditionTrainsitionFromAll) == 0x2C &&
                  offsetof(cAIFSMNode, mConditionTrainsitionFromAllId) == 0x30, "0x010CAA00");

struct cAIFSMCluster {                    // MtDTI size 0xC4, vftable 0x0143FFCC
    uint32_t vftable;
    uint32_t mId;
    uint32_t mOwnerNodeUniqueId;          // 0x00E06A9F
    uint32_t mInitialStateId;             // 0x00E06AB7: the start, looked up by id
    uint32_t nodeCount;                   // getNode 0x010C9270, getNodeById 0x010C9290
    cAIFSMNode* nodes;
    uint8_t rest[0xC4 - 0x18];
};
static_assert(sizeof(cAIFSMCluster) == 0xC4, "MtDTI size");
static_assert(offsetof(cAIFSMCluster, nodeCount) == 0x10 && offsetof(cAIFSMCluster, nodes) == 0x14, "0x010C9290");

struct OnceList {                         // the entries used once (0x00E05A00 searches it)
    uint8_t head[0x0C];
    uint32_t count;
    uint32_t* uniqueIds;
};
static_assert(offsetof(OnceList, count) == 0x0C && offsetof(OnceList, uniqueIds) == 0x10, "0x00E05A00");

struct ClusterDriveInfo {                 // cAIFSM::Core::ClusterDriveInfo, MtDTI size 0x28
    uint32_t vftable;
    cAIFSMCluster* cluster;               // 0x00E0673F
    OnceList* once;                       // 0x00E06767
    cAIFSMNode* current;                  // 0x00E06756
    cAIFSMNode* next;                     // written at 0x00E067CF
    uint8_t go;                           // +0x14: a transition was chosen
    uint8_t byEntry;                      // +0x15: it came through an entry from any state
    uint8_t lock;                         // +0x16: nonzero keeps the previous choice
    uint8_t pad17;
    uint32_t timerRunning;                // +0x18: no check while it is set (0x00E06720)
    float timer;
    uint8_t rest[0x28 - 0x20];
};
static_assert(sizeof(ClusterDriveInfo) == 0x28, "MtDTI size, and the drive stride (0x00E0936B)");
static_assert(offsetof(ClusterDriveInfo, next) == 0x10 && offsetof(ClusterDriveInfo, go) == 0x14 &&
                  offsetof(ClusterDriveInfo, timerRunning) == 0x18, "0x00E06710");

struct TreeInfo {                         // cAIConditionTree::TreeInfo, MtDTI size 0x34
    uint8_t head[0x2C];
    uint32_t id;                          // compared at 0x0117AAB6
    void* root;                           // 0x01179B75: the work node evaluated
};
static_assert(sizeof(TreeInfo) == 0x34, "MtDTI size, and the lookup's stride (0x0117AABB)");

struct cAIConditionTree {                 // MtDTI size 0x70, vftable 0x01444D7C
    uint32_t vftable;
    uint32_t pad04;
    uint32_t resultType;                  // 0x01179B9E: 1 bool, 2 s32, 3 f32, 6 an operation ...
    uint8_t resultBool;
    uint8_t pad0D[3];
    uint32_t resultS32;
    uint8_t pad14[4];
    uint64_t resultS64;
    uint8_t pad20[0x54 - 0x20];
    uint32_t infoCount;                   // 0x0117AAA2
    TreeInfo* infos;
    uint8_t rest[0x70 - 0x5C];
};
static_assert(sizeof(cAIConditionTree) == 0x70, "MtDTI size");
static_assert(offsetof(cAIConditionTree, resultType) == 0x08 && offsetof(cAIConditionTree, resultS32) == 0x10 &&
                  offsetof(cAIConditionTree, infoCount) == 0x54 && offsetof(cAIConditionTree, infos) == 0x58,
              "0x0117AAA0, 0x01179B70, 0x01179C90");

struct cAIFSMCore {                       // cAIFSM::Core, MtDTI size 0xF0
    uint8_t head[0x30];
    cAIConditionTree conditions;          // 0x00E0583C: lea ecx, [core + 0x30]
    uint32_t stateCallback;               // +0xA0, called on a change (0x00E09403)
    uint32_t padA4;
    uint32_t mAttribute;                  // +0xA8: bit 1 links, bit 2 entries; 3 when made (0x00E05797)
    uint8_t rest[0xF0 - 0xAC];
};
static_assert(sizeof(cAIFSMCore) == 0xF0, "MtDTI size, and cAIFSM::move's stride (0x00E09910)");
static_assert(offsetof(cAIFSMCore, conditions) == 0x30 && offsetof(cAIFSMCore, mAttribute) == 0xA8, "0x00E06731");

// Condition nodes: the resources (rAIConditionTree::*) and the work nodes built over them.
struct OperationNodeRes {                 // rAIConditionTree::OperationNode, MtDTI size 0x18
    uint8_t head[0x14];
    uint32_t mOperator;                   // 0x0117C06F
};
static_assert(sizeof(OperationNodeRes) == 0x18, "MtDTI size");

struct ConstS32NodeRes {                  // rAIConditionTree::ConstS32Node, MtDTI size 0x1C, vftable 0x014486E8
    uint32_t vftable;
    uint8_t pad04[0x10];
    int32_t mValue;                       // 0x01206F90
    uint8_t mIsBitNo;                     // 0x01206F82: set = the value is a bit number
    uint8_t pad19[3];
};
static_assert(sizeof(ConstS32NodeRes) == 0x1C && offsetof(ConstS32NodeRes, mValue) == 0x14, "MtDTI size, 0x01206F90");

struct ConstF32NodeRes {                  // rAIConditionTree::ConstF32Node, MtDTI size 0x18, vftable 0x01448788
    uint32_t vftable;
    uint8_t pad04[0x10];
    float mValue;                         // 0x00572680
};
static_assert(sizeof(ConstF32NodeRes) == 0x18, "MtDTI size");

struct OperationWorkNode {                // cAIConditionTree::OperationWorkNode, MtDTI size 0x1C, vftable 0x01444D90
    uint32_t vftable;
    OperationNodeRes* res;                // 0x0117C066
    uint32_t count;                       // 0x0117C055
    void** children;                      // 0x0117C069
    uint8_t pad10[8];
    uint32_t result;                      // 0x0117C089
};
static_assert(sizeof(OperationWorkNode) == 0x1C && offsetof(OperationWorkNode, result) == 0x18, "MtDTI size, 0x0117C040");

struct ConstWorkNode {                    // cAIConditionTree::ConstWorkNode, MtDTI size 0x18, vftable 0x01444DD0
    uint32_t vftable;
    void* res;                            // 0x0117C1C6: every getter forwards to it
    uint8_t rest[0x18 - 0x08];
};
static_assert(sizeof(ConstWorkNode) == 0x18, "MtDTI size");
