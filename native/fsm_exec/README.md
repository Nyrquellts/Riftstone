# fsm_exec (Riftstone test harness)

Runs Dark Arisen's own state-machine code on made-up machines, so `src/riftstone/fsmcheck.py` is
checked against the code it describes rather than against a reading of it. Nothing ships from here:
it is a test.

- `fsm_stub.exe` owns DDDA.exe's fixed address range (the same trick as the plugin harnesses);
  `fsm_exec_core.dll` maps the owner's `DDDA.exe` over it, checks that the routines start with the
  bytes this was written for (build 2364871), and calls them on objects it builds. Nothing is patched
  and the game is not launched.
- `src/fsm_layout.h` types those objects: each struct's size is the class's own MtDTI size (the
  engine's registry, `ddon re`) and each field sits where the game's code reads it (the routine is
  named beside it); `static_assert`s hold both, so a wrong reading fails the build, and running the
  routines on them fails the test.
- **Transition cases:** `cAIFSM::Core`'s transition check (`0x00E06710`), which walks the states
  entered from any state and then the current state's links (`0x00E05800`), looks conditions up by id
  (`0x0117AAA0`) and reads their results (`0x01179B70`, `0x01179C90`). The condition roots are stand-ins
  whose result the case gives, so only the transition logic is exercised.
- **Condition cases:** the game's own `OperationWorkNode` over `ConstWorkNode` operands holding
  `ConstS32Node` / `ConstF32Node` resources, through the same lookup: every operator 0..18, 99 and
  0xFFFFFFFF, with no to three operands, integers and floats, and nested operations.

`test/run_tests.py` writes 6,000 random transition cases and about 5,500 conditions, runs the harness,
and compares every result with `fsmcheck.step` and with fsmcheck's verdicts (always / never; the
conditions fsmcheck leaves open are counted). `docs/formats.md` ("FSM" and "How the game runs a
machine") has what it found. Online's routines were read, not run.

Every count in a case file (states, links, conditions, operands, the once-list) is checked against the
16 MB arena before its size is computed, so a count whose size does not fit in 32 bits is refused ("the
case needs more than ... bytes") instead of wrapping round to a small block that the case then writes
past; `run_tests.py` feeds four such case files first.

```bat
native\fsm_exec\build.cmd
python native\fsm_exec\test\run_tests.py
```

Needs Visual Studio 2022 with the C++ x86 tools; without the build or the game the test reports a skip.
