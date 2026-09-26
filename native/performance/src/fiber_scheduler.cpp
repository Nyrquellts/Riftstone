#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "fiber_scheduler.hpp"
#include "work_stealing.hpp"
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace riftstone::perf {
TaskId TaskGraph::add(std::function<void(FiberContext&)> function, std::span<const TaskId> dependencies) {
    if (!function || tasks_.size() >= (1u << 20)) throw std::invalid_argument("invalid task or graph limit");
    std::vector<TaskId> deps(dependencies.begin(), dependencies.end());
    std::sort(deps.begin(), deps.end());
    if (std::adjacent_find(deps.begin(), deps.end()) != deps.end() ||
        (!deps.empty() && deps.back() >= tasks_.size())) throw std::invalid_argument("dependency must be a unique earlier task");
    // Reserve all mutating vectors before changing the graph's meaning.
    for (auto id : deps) tasks_[id].successors.reserve(tasks_[id].successors.size() + 1);
    const auto id = tasks_.size();
    tasks_.push_back({std::move(function), std::move(deps), {}});
    for (auto predecessor : tasks_.back().dependencies) tasks_[predecessor].successors.push_back(id);
    return id;
}

struct SchedulerImpl {
    struct Cancelled {};
    struct Node { TaskId id = 0; std::atomic<std::size_t> dependencies{0}; };
    struct Worker;
    enum class State { free, runnable, executing };
    struct Fiber { Worker* worker = nullptr; void* handle = nullptr; Node* node = nullptr; State state = State::free; };
    struct Worker {
        SchedulerImpl* owner;
        std::size_t index;
        WorkDeque<Node*> queue;
        std::vector<Fiber> fibers;
        std::thread thread;
        void* scheduler = nullptr;
        SchedulerStats stats;
        Worker(SchedulerImpl* o, std::size_t i) : owner(o), index(i), queue(o->config.capacity), fibers(o->config.fibers_per_worker) {}
    };
    const SchedulerConfig config;
    std::unique_ptr<Node[]> nodes;
    std::vector<std::unique_ptr<Worker>> workers;
    std::mutex control, gate, error_gate;
    std::condition_variable ready, done;
    std::size_t initialized = 0, finished = 0;
    std::uint64_t generation = 0;
    bool stopping = false;
    const TaskGraph* graph = nullptr;
    std::atomic<std::size_t> remaining{0};
    std::atomic<bool> cancelled{false};
    std::exception_ptr error;
    static thread_local Worker* current_worker;

    static SchedulerConfig validate(SchedulerConfig c) {
        if (c.workers < 1 || c.workers > 32 || c.capacity < 2 || c.capacity > (1u << 20) ||
            (c.capacity & (c.capacity - 1)) || c.fibers_per_worker < 1 || c.fibers_per_worker > 128 ||
            c.stack_reserve < 65536 || c.stack_reserve > (8u << 20) ||
            c.workers * c.fibers_per_worker * static_cast<std::uint64_t>(c.stack_reserve) > (512ull << 20))
            throw std::invalid_argument("scheduler configuration exceeds bounded worker, queue or stack limits");
        return c;
    }
    explicit SchedulerImpl(SchedulerConfig c) : config(validate(c)), nodes(std::make_unique<Node[]>(config.capacity)) {
        try {
            workers.reserve(config.workers);
            for (std::size_t i = 0; i < config.workers; ++i) workers.push_back(std::make_unique<Worker>(this, i));
            for (auto& w : workers) w->thread = std::thread([this, p=w.get()] { worker_main(*p); });
            std::unique_lock lock(gate);
            done.wait(lock, [&] { return initialized == workers.size(); });
            if (error) { lock.unlock(); std::rethrow_exception(error); }
        } catch (...) { shutdown(); throw; }
    }
    ~SchedulerImpl() { shutdown(); }
    void shutdown() noexcept {
        { std::lock_guard lock(gate); stopping = true; }
        ready.notify_all();
        for (auto& w : workers) if (w->thread.joinable()) w->thread.join();
    }
    void fail(std::exception_ptr failure) noexcept {
        { std::lock_guard lock(error_gate); if (!error) error = std::move(failure); }
        cancelled.store(true, std::memory_order_release);
    }
    static VOID WINAPI fiber_main(void* value) {
        auto& f = *static_cast<Fiber*>(value);
        auto& w = *f.worker;
        auto& owner = *w.owner;
        for (;;) {
            FiberContext context(&w, &f);
            try {
                if (owner.cancelled.load(std::memory_order_acquire)) throw Cancelled{};
                owner.graph->tasks_[f.node->id].function(context);
            } catch (const Cancelled&) {
            } catch (...) { owner.fail(std::current_exception()); }
            if (!owner.cancelled.load(std::memory_order_acquire)) {
                for (auto id : owner.graph->tasks_[f.node->id].successors) {
                    auto& node = owner.nodes[id];
                    if (node.dependencies.fetch_sub(1, std::memory_order_acq_rel) == 1 && !w.queue.push(&node))
                        owner.fail(std::make_exception_ptr(std::runtime_error("ready deque capacity exhausted")));
                }
            }
            ++w.stats.completed;
            owner.remaining.fetch_sub(1, std::memory_order_acq_rel);
            f.node = nullptr;
            f.state = State::free;
            SwitchToFiber(w.scheduler);
        }
    }
    void resume(Worker& w, Fiber& f) {
        f.state = State::executing;
        ++w.stats.fiber_switches;
        SwitchToFiber(f.handle);
    }
    void execute(Worker& w) {
        std::size_t cursor = 0;
        bool prefer_resume = false;
        while (remaining.load(std::memory_order_acquire) != 0) {
            Fiber* suspended = nullptr;
            Fiber* unused = nullptr;
            for (std::size_t i = 0; i < w.fibers.size(); ++i) {
                auto& f = w.fibers[(cursor + i) % w.fibers.size()];
                if (!suspended && f.state == State::runnable) suspended = &f;
                if (!unused && f.state == State::free) unused = &f;
            }
            cursor = (cursor + 1) % w.fibers.size();
            if (cancelled.load(std::memory_order_acquire)) {
                // Resume suspended frames so yield throws and C++ destructors
                // unwind on their own worker before deleting any fiber stack.
                if (suspended) { resume(w, *suspended); continue; }
                break;
            }
            if (suspended && (prefer_resume || !unused)) {
                resume(w, *suspended); prefer_resume = false; continue;
            }
            Node* task = nullptr;
            if (unused) {
                task = w.queue.pop();
                if (!task) for (std::size_t i = 1; i < workers.size(); ++i) {
                    task = workers[(w.index + i) % workers.size()]->queue.steal();
                    if (task) { ++w.stats.stolen; break; }
                }
            }
            if (task) {
                unused->node = task;
                resume(w, *unused); prefer_resume = true;
            } else if (suspended) { resume(w, *suspended); prefer_resume = false; }
            else { ++w.stats.idle_yields; std::this_thread::yield(); }
        }
    }
    void worker_main(Worker& w) noexcept {
        current_worker = &w;
        try {
            w.scheduler = ConvertThreadToFiberEx(nullptr, FIBER_FLAG_FLOAT_SWITCH);
            if (!w.scheduler) throw std::runtime_error("ConvertThreadToFiberEx failed");
            for (auto& f : w.fibers) {
                f.worker = &w;
                f.handle = CreateFiberEx(16384, config.stack_reserve, FIBER_FLAG_FLOAT_SWITCH, fiber_main, &f);
                if (!f.handle) throw std::runtime_error("CreateFiberEx failed");
            }
        } catch (...) { fail(std::current_exception()); }
        {
            std::lock_guard lock(gate); ++initialized;
        }
        done.notify_one();
        std::uint64_t seen = 0;
        for (;;) {
            std::unique_lock lock(gate);
            ready.wait(lock, [&] { return stopping || generation != seen; });
            if (stopping) break;
            seen = generation;
            lock.unlock();
            execute(w);
            lock.lock(); ++finished; lock.unlock(); done.notify_one();
        }
        for (auto& f : w.fibers) if (f.handle) DeleteFiber(f.handle);
        if (w.scheduler) ConvertFiberToThread();
        current_worker = nullptr;
    }
    SchedulerStats run(const TaskGraph& input) {
        if (current_worker) throw std::logic_error("run cannot be called from a scheduler job");
        std::unique_lock single(control, std::try_to_lock);
        if (!single.owns_lock()) throw std::logic_error("concurrent run is refused");
        if (input.size() > config.capacity) throw std::length_error("task graph exceeds scheduler capacity");
        if (input.size() == 0) return {};
        cancelled.store(false);
        error = nullptr;
        graph = &input;
        for (auto& w : workers) { w->queue.reset_quiescent(); w->stats = {}; }
        for (std::size_t i = 0; i < input.size(); ++i) {
            nodes[i].id = i;
            nodes[i].dependencies.store(input.tasks_[i].dependencies.size(), std::memory_order_relaxed);
        }
        std::size_t next = 0;
        for (std::size_t i = 0; i < input.size(); ++i) if (input.tasks_[i].dependencies.empty()) {
            if (!workers[next++ % workers.size()]->queue.push(&nodes[i]))
                throw std::logic_error("initial deque capacity exhausted");
        }
        remaining.store(input.size(), std::memory_order_release);
        std::unique_lock lock(gate);
        finished = 0; ++generation;
        ready.notify_all();
        done.wait(lock, [&] { return finished == workers.size(); });
        SchedulerStats result;
        for (auto& w : workers) {
            result.completed += w->stats.completed;
            result.stolen += w->stats.stolen;
            result.fiber_switches += w->stats.fiber_switches;
            result.idle_yields += w->stats.idle_yields;
            result.workers_used += w->stats.completed != 0;
        }
        graph = nullptr;
        if (error) std::rethrow_exception(error);
        return result;
    }
};
thread_local SchedulerImpl::Worker* SchedulerImpl::current_worker = nullptr;
void FiberContext::yield() {
    auto& w = *static_cast<SchedulerImpl::Worker*>(worker_);
    auto& f = *static_cast<SchedulerImpl::Fiber*>(fiber_);
    if (w.owner->cancelled.load(std::memory_order_acquire)) throw SchedulerImpl::Cancelled{};
    f.state = SchedulerImpl::State::runnable;
    SwitchToFiber(w.scheduler);
    if (w.owner->cancelled.load(std::memory_order_acquire)) throw SchedulerImpl::Cancelled{};
}
std::size_t FiberContext::worker_index() const noexcept {
    return static_cast<SchedulerImpl::Worker*>(worker_)->index;
}
Scheduler::Scheduler(SchedulerConfig config) : impl_(std::make_unique<SchedulerImpl>(config)) {}
Scheduler::~Scheduler() = default;
SchedulerStats Scheduler::run(const TaskGraph& graph) { return impl_->run(graph); }
}
