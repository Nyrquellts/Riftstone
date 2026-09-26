"""A small constraint solver in the manner of WaveFunctionCollapse, complete by backtracking.

WaveFunctionCollapse (M. Gumin) keeps every variable's remaining choices, collapses the one with the least
Shannon entropy to a weighted random choice and propagates the consequences; Karth and Smith showed that
this is constraint solving with a particular variable and value order.  This solver is exactly that, with
two additions that make its answers claims rather than hopes:

* propagation is arc consistency (AC-3) over binary constraints, repeated after every choice, so a choice
  whose consequences empty some variable's domain is seen at once;
* a contradiction backtracks to the last choice and tries the next value, so ``solve`` either returns an
  assignment that satisfies every constraint (checked again at the end), or says the problem has no
  solution (the search was exhausted), or says it ran out of its budget -- never a partial answer.

Everything is deterministic: the same problem and seed give the same answer on every machine (random
choices come from ``random.Random(seed)``; ties in entropy are broken by that generator, then by the
variables' order).  Standard library only.

    p = Problem(["a", "b"], {"a": [1, 2], "b": [1, 2]})
    p.constrain("a", "b", lambda x, y: x < y)
    solve(p, seed=7).assignment   # {'a': 1, 'b': 2}
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable

from .errors import RiftError


class Unsatisfiable(RiftError):
    """Every assignment was tried or ruled out: the constraints cannot all hold."""


class OutOfBudget(RiftError):
    """The search stopped at its budget; whether a solution exists is UNKNOWN."""


@dataclass
class Problem:
    variables: list
    domains: dict                                   # variable -> ordered list of values
    weights: dict = field(default_factory=dict)     # variable -> {value: weight > 0}; missing = 1
    arcs: dict = field(default_factory=dict)        # (x, y) -> [test(value_x, value_y)]
    checks: list = field(default_factory=list)      # test(partial assignment) -> None or a reason

    def __post_init__(self):
        if len(set(self.variables)) != len(self.variables):
            raise RiftError("a problem's variables must be distinct")
        for v in self.variables:
            if v not in self.domains:
                raise RiftError(f"variable {v!r} has no domain")
            self.domains[v] = list(dict.fromkeys(self.domains[v]))

    def constrain(self, x: Hashable, y: Hashable, test: Callable[[Any, Any], bool]) -> None:
        """A binary constraint: test(value of x, value of y) must hold."""
        if x not in self.domains or y not in self.domains or x == y:
            raise RiftError(f"a constraint joins two different variables of the problem ({x!r}, {y!r})")
        self.arcs.setdefault((x, y), []).append(test)
        self.arcs.setdefault((y, x), []).append(lambda b, a, t=test: t(a, b))

    def all_different(self, variables) -> None:
        vs = list(variables)
        for i, x in enumerate(vs):
            for y in vs[i + 1:]:
                self.constrain(x, y, lambda a, b: a != b)

    def require(self, check: Callable[[dict], Any]) -> None:
        """A constraint over many variables, checked on every partial assignment: it returns None when the
        assignment can still be completed, or a reason when it cannot."""
        self.checks.append(check)

    def satisfied(self, assignment: dict) -> bool:
        return self.violations(assignment) == []

    def violations(self, assignment: dict) -> list:
        out = []
        for v in self.variables:
            if v not in assignment:
                out.append(f"{v!r} has no value")
            elif assignment[v] not in self.domains[v]:
                out.append(f"{v!r} = {assignment[v]!r} is outside its domain")
        for (x, y), tests in self.arcs.items():
            if x in assignment and y in assignment and not all(t(assignment[x], assignment[y]) for t in tests):
                out.append(f"{x!r} = {assignment[x]!r} and {y!r} = {assignment[y]!r} break a constraint")
        for check in self.checks:
            why = check(assignment)
            if why:
                out.append(str(why))
        return out


@dataclass
class Solution:
    assignment: dict
    choices: int            # values collapsed
    backtracks: int
    revisions: int          # arc revisions made by propagation
    order: list             # variables in the order they were collapsed


def _entropy(values, weights) -> float:
    ws = [weights.get(v, 1.0) for v in values]
    total = sum(ws)
    return math.log(total) - sum(w * math.log(w) for w in ws) / total


def solve(problem: Problem, seed: int = 0, budget: int = 20000, max_checks: int = 5_000_000) -> Solution:
    """An assignment satisfying every constraint (Unsatisfiable when none exists, OutOfBudget when the
    search needed more than ``budget`` backtracks or ``max_checks`` constraint tests)."""
    for v in problem.variables:
        for value, w in problem.weights.get(v, {}).items():
            if not (isinstance(w, (int, float)) and w > 0 and math.isfinite(w)):
                raise RiftError(f"variable {v!r}: the weight of {value!r} must be a positive number")
    rng = random.Random(seed)
    neighbours = {v: [] for v in problem.variables}
    for (x, y) in problem.arcs:
        neighbours[x].append(y)
    stats = {"choices": 0, "backtracks": 0, "revisions": 0, "checks": 0}

    def revise(domains, x, y) -> bool:
        tests = problem.arcs[(x, y)]
        ys = domains[y]
        keep, checks = [], 0
        for a in domains[x]:
            for b in ys:
                checks += 1
                if all(t(a, b) for t in tests):
                    keep.append(a)
                    break
        stats["revisions"] += 1
        stats["checks"] += checks
        if stats["checks"] > max_checks:
            raise OutOfBudget(f"no answer within {max_checks} constraint tests; whether one exists is UNKNOWN")
        if len(keep) != len(domains[x]):
            domains[x] = keep
            return True
        return False

    def propagate(domains, queue) -> bool:
        pending = set(queue)
        while queue:
            x, y = queue.popleft()
            pending.discard((x, y))
            if revise(domains, x, y):
                if not domains[x]:
                    return False
                for z in neighbours[x]:
                    if z != y and (z, x) not in pending:
                        queue.append((z, x))
                        pending.add((z, x))
        return True

    def checks_ok(assignment) -> bool:
        return all(not c(assignment) for c in problem.checks)

    domains = {v: list(problem.domains[v]) for v in problem.variables}
    if any(not d for d in domains.values()) or not propagate(domains, deque(problem.arcs)):
        raise Unsatisfiable("the constraints rule out every value of some variable before any choice")
    noise = {v: rng.random() * 1e-6 for v in problem.variables}     # Gumin's tie-breaking noise
    order: list = []
    # each frame: (domains before the choice, variable, values still to try)
    stack = []
    assignment: dict = {}
    while True:
        open_vars = [v for v in problem.variables if v not in assignment]
        if not open_vars:
            if not problem.satisfied(assignment):          # belt and braces: the answer is re-checked
                raise RiftError("internal: the solver produced an assignment that breaks a constraint")
            return Solution(dict(assignment), stats["choices"], stats["backtracks"], stats["revisions"], order)
        var = min(open_vars, key=lambda v: (_entropy(domains[v], problem.weights.get(v, {})) + noise[v],
                                            problem.variables.index(v)))
        values = _weighted_order(domains[var], problem.weights.get(var, {}), rng)
        stack.append(({v: list(d) for v, d in domains.items()}, var, values))
        while True:
            saved, var, values = stack[-1]
            placed = False
            while values:
                value = values.pop(0)
                trial = {v: list(d) for v, d in saved.items()}
                trial[var] = [value]
                assignment[var] = value
                stats["choices"] += 1
                if checks_ok(assignment) and propagate(trial, deque((z, var) for z in neighbours[var])):
                    domains = trial
                    order.append(var)
                    placed = True
                    break
                del assignment[var]
            if placed:
                break
            stack.pop()
            if not stack:
                raise Unsatisfiable("every value of every variable was tried: the constraints cannot all hold")
            stats["backtracks"] += 1
            if stats["backtracks"] > budget:
                raise OutOfBudget(f"no answer within {budget} backtracks; whether one exists is UNKNOWN")
            prev = stack[-1][1]
            if order and order[-1] == prev:
                order.pop()
            assignment.pop(prev, None)


def _weighted_order(values, weights, rng: random.Random) -> list:
    """The values in a weighted random order (Efraimidis-Spirakis keys): heavier values tend to come first,
    and each order is reproducible from the generator."""
    keyed = []
    for i, v in enumerate(values):
        w = weights.get(v, 1.0)
        u = rng.random()
        keyed.append((-(math.log(u) / w) if u > 0 else math.inf, i, v))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [v for _, _, v in keyed]
