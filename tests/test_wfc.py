"""The constraint solver behind level synthesis: answers satisfy every constraint, impossibility is proved
by exhausting the search (never guessed), the budget is honoured, and the same seed gives the same answer."""
import unittest
from collections import Counter

import helpers  # noqa: F401 (sys.path)
from riftstone import wfc
from riftstone.errors import RiftError


def australia(colours):
    regions = ["WA", "NT", "SA", "Q", "NSW", "V", "T"]
    p = wfc.Problem(regions, {r: list(colours) for r in regions})
    for a, b in [("WA", "NT"), ("WA", "SA"), ("NT", "SA"), ("NT", "Q"), ("SA", "Q"), ("SA", "NSW"),
                 ("SA", "V"), ("Q", "NSW"), ("NSW", "V")]:
        p.constrain(a, b, lambda x, y: x != y)
    return p


def queens(n):
    p = wfc.Problem(list(range(n)), {i: list(range(n)) for i in range(n)})
    for i in range(n):
        for j in range(i + 1, n):
            p.constrain(i, j, lambda a, b, d=j - i: a != b and abs(a - b) != d)
    return p


class SolverTest(unittest.TestCase):
    def test_a_binary_constraint(self):
        p = wfc.Problem(["a", "b"], {"a": [1, 2], "b": [1, 2]})
        p.constrain("a", "b", lambda x, y: x < y)
        self.assertEqual(wfc.solve(p, seed=7).assignment, {"a": 1, "b": 2})

    def test_map_colouring_three_colours_solves_two_is_proved_impossible(self):
        s = wfc.solve(australia("rgb"), seed=3)
        self.assertTrue(australia("rgb").satisfied(s.assignment))
        with self.assertRaises(wfc.Unsatisfiable):
            wfc.solve(australia("rg"), seed=3)

    def test_eight_queens(self):
        for seed in range(5):
            s = wfc.solve(queens(8), seed=seed)
            self.assertEqual(queens(8).violations(s.assignment), [])

    def test_pigeonhole_is_unsatisfiable_or_out_of_budget_never_an_answer(self):
        p = wfc.Problem(list(range(6)), {i: list(range(5)) for i in range(6)})
        p.all_different(range(6))
        with self.assertRaises(wfc.Unsatisfiable):
            wfc.solve(p, budget=10 ** 6)
        with self.assertRaises((wfc.Unsatisfiable, wfc.OutOfBudget)):
            wfc.solve(p, budget=3)

    def test_the_same_seed_gives_the_same_answer(self):
        a = [wfc.solve(queens(8), seed=11).assignment for _ in range(3)]
        self.assertEqual(a[0], a[1])
        self.assertEqual(a[1], a[2])
        answers = {tuple(sorted(wfc.solve(queens(8), seed=s).assignment.items())) for s in range(12)}
        self.assertGreater(len(answers), 1, "different seeds should explore different answers")

    def test_weights_steer_the_choice(self):
        picks = Counter()
        for seed in range(200):
            p = wfc.Problem(["x"], {"x": ["common", "rare"]}, weights={"x": {"common": 9.0, "rare": 1.0}})
            picks[wfc.solve(p, seed=seed).assignment["x"]] += 1
        self.assertGreater(picks["common"], 150)
        self.assertGreater(picks["rare"], 0)

    def test_bad_weights_and_bad_constraints_are_refused(self):
        with self.assertRaises(RiftError):
            wfc.solve(wfc.Problem(["x"], {"x": [1]}, weights={"x": {1: 0.0}}))
        p = wfc.Problem(["x"], {"x": [1]})
        with self.assertRaises(RiftError):
            p.constrain("x", "x", lambda a, b: True)
        with self.assertRaises(RiftError):
            wfc.Problem(["x", "x"], {"x": [1]})

    def test_a_global_check_prunes_partial_assignments(self):
        # three digits summing to exactly 5
        p = wfc.Problem(["a", "b", "c"], {v: list(range(4)) for v in "abc"})
        p.require(lambda s: "over" if sum(s.values()) > 5 else
                  ("short" if len(s) == 3 and sum(s.values()) != 5 else None))
        for seed in range(10):
            s = wfc.solve(p, seed=seed).assignment
            self.assertEqual(sum(s.values()), 5)
        self.assertIn("over", " ".join(p.violations({"a": 3, "b": 3, "c": 0})))

    def test_wave_function_collapse_on_tiles_with_a_path_constraint(self):
        """Tiles on a 5x5 grid; neighbouring edges must match (open meets open); a path of open tiles
        must join the left middle to the right middle.  The path check is optimistic on unassigned
        cells, so it prunes only assignments that can no longer be completed."""
        W = H = 5
        tiles = {"wall": (0, 0, 0, 0), "h": (0, 1, 0, 1), "v": (1, 0, 1, 0), "x": (1, 1, 1, 1)}
        cells = [(x, y) for y in range(H) for x in range(W)]
        p = wfc.Problem(cells, {c: list(tiles) for c in cells}, weights={c: {"wall": 6.0} for c in cells})
        for (x, y) in cells:                       # edges: 0 up, 1 right, 2 down, 3 left
            if x + 1 < W:
                p.constrain((x, y), (x + 1, y), lambda a, b: tiles[a][1] == tiles[b][3])
            if y + 1 < H:
                p.constrain((x, y), (x, y + 1), lambda a, b: tiles[a][2] == tiles[b][0])
        start, goal = (0, 2), (W - 1, 2)

        def path(s):
            def open_(c):
                return c not in s or s[c] != "wall"
            seen, todo = {start}, [start] if open_(start) else []
            while todo:
                c = todo.pop()
                if c == goal:
                    return None
                x, y = c
                for d, (dx, dy) in enumerate(((0, -1), (1, 0), (0, 1), (-1, 0))):
                    n = (x + dx, y + dy)
                    if n in seen or not (0 <= n[0] < W and 0 <= n[1] < H) or not open_(n):
                        continue
                    if c in s and not tiles[s[c]][d]:
                        continue
                    if n in s and not tiles[s[n]][(d + 2) % 4]:
                        continue
                    seen.add(n)
                    todo.append(n)
            return "no open path from the left door to the right door"
        p.require(path)
        for seed in range(6):
            s = wfc.solve(p, seed=seed, budget=5000).assignment
            self.assertEqual(p.violations(s), [])
            self.assertIsNone(path(s))


if __name__ == "__main__":
    unittest.main()
