#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <span>
#include <vector>

namespace riftstone::perf {
class Scheduler;
class FiberContext {
    void* worker_;
    void* fiber_;
    friend class Scheduler;
    friend struct SchedulerImpl;
    FiberContext(void* worker, void* fiber) : worker_(worker), fiber_(fiber) {}
public:
    FiberContext(const FiberContext&) = delete;
    FiberContext& operator=(const FiberContext&) = delete;
    // Cooperative suspension; resumed only on the same worker OS thread.
    // Never hold a non-reentrant OS mutex or engine lock across this call.
    void yield();
    std::size_t worker_index() const noexcept;
};
using TaskId = std::size_t;
class TaskGraph {
    struct Task {
        std::function<void(FiberContext&)> function;
        std::vector<TaskId> dependencies;
        std::vector<TaskId> successors;
    };
    std::vector<Task> tasks_;
    friend struct SchedulerImpl;
public:
    // Dependencies must be existing nodes. This makes cycles unrepresentable.
    TaskId add(std::function<void(FiberContext&)> function, std::span<const TaskId> dependencies = {});
    std::size_t size() const noexcept { return tasks_.size(); }
};
struct SchedulerConfig {
    std::size_t workers = 4;
    std::size_t capacity = 4096;
    std::size_t fibers_per_worker = 8;
    std::size_t stack_reserve = 256 * 1024;
};
struct SchedulerStats {
    std::uint64_t completed = 0;
    std::uint64_t stolen = 0;
    std::uint64_t fiber_switches = 0;
    std::uint64_t idle_yields = 0;
    std::size_t workers_used = 0;
};
struct SchedulerImpl;
class Scheduler {
    std::unique_ptr<SchedulerImpl> impl_;
public:
    explicit Scheduler(SchedulerConfig config = {});
    ~Scheduler();
    Scheduler(const Scheduler&) = delete;
    Scheduler& operator=(const Scheduler&) = delete;
    // Synchronous frame barrier. Workers persist between calls. The graph and
    // captured objects must stay alive and unmodified until run returns.
    // One control thread at a time; a callback must not recursively call run.
    SchedulerStats run(const TaskGraph& graph);
};
}
