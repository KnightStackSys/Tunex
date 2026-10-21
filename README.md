# ⚙️ Tunex

♟️ **UCI tuning and strength testing for IronPhoenix 4-player chess.**

**Tunex** is a lightweight Python tuning and strength-testing harness built for [IronPhoenix](https://github.com/KnightStackSys/IronPhoenix), a 4-player chess Teams engine.

It keeps chess computation inside the C++ engine and uses Python for the parts that benefit from rapid iteration: launching engine processes, applying UCI parameters, paired match testing, Elo statistics, SPRT, SPSA, experiment logs, and resumable tuning state.

## ✨ What it supports

- **UCI runtime parameters** — change IronPhoenix search/HCE values without recompiling.
- **4PC Teams match runner** — candidate plays Red+Yellow in one game and Blue+Green in the paired game.
- **Paired openings** — the same opening is reused when teams are swapped.
- **Fixed movetime, node, or depth testing**.
- **W/D/L, score, Elo, and approximate 95% confidence intervals**.
- **Sequential SPRT** for isolated search/evaluation experiments.
- **SPSA** for tuning multiple integer UCI parameters at once.
- **Parallel games** with configurable workers.
- **JSONL game logs** and **resumable SPSA state**.
- Windows paths work normally; no shell quoting is required.

Tunex currently targets IronPhoenix's 4PC UCI behavior, including its immediate king-capture terminal score (`mate 1`).

## 📋 Requirements

- Python **3.11+**
- A built IronPhoenix executable
- IronPhoenix with runtime tuning UCI options (the `runtime-tuning-parameters` work in IronPhoenix)

Tunex itself has no third-party runtime dependencies.

## 🛠️ Install

From the Tunex repository:

```bash
python -m pip install -e .
```

Then:

```bash
tunex --help
```

## ⚙️ Configure IronPhoenix

Copy `configs/ironphoenix.example.json` to `configs/ironphoenix.json` and change `engine.path` to your executable.

A typical Windows path is:

```json
{
  "engine": {
    "path": "C:/Users/you/Documents/GitHub/IronPhoenix/out/build/x64-release/ironphoenix.exe"
  }
}
```

Forward slashes are convenient in JSON even on Windows.

Recommended common options for search-parameter tuning are:

```json
"common_options": {
  "Hash": 64,
  "MultiPV": 1,
  "UseNNUE": false
}
```

Keeping NNUE off is useful when tuning HCE or search behavior against a stable HCE baseline. Turn it on deliberately when you want NNUE-backed search tests.

## 🔎 Verify the engine/options

```bash
tunex -c configs/ironphoenix.json probe
```

Tunex performs a UCI handshake, validates configured option names, applies the options, waits for `readyok`, and asks IronPhoenix for `tuneparams`.

If a configured tuning option is not advertised, Tunex fails early instead of silently running a useless match.

## ♟️ Run a fixed match

```bash
tunex -c configs/ironphoenix.json match
```

Override the configured game count:

```bash
tunex -c configs/ironphoenix.json match --games 200
```

With paired testing, game 1 assigns the candidate to **Red+Yellow** and game 2 assigns it to **Blue+Green**, using the same opening.

Every completed game is appended to the configured JSONL result file.

## 📖 Openings

Set:

```json
"openings_file": "openings.txt"
```

Each non-comment line can be a move sequence:

```text
moves f4f6 a8g2 g13l8
```

or simply:

```text
f4f6 a8g2 g13l8
```

Custom FEN4 positions are supported too:

```text
fen <FEN4> | moves f4f6 a8g2
```

Paired games always reuse the same opening before swapping the candidate's team.

## 📈 SPRT

Use SPRT for questions such as: *Does this LMP change gain at least a small amount of Elo?*

Configure:

```json
"sprt": {
  "elo0": 0,
  "elo1": 5,
  "alpha": 0.05,
  "beta": 0.05,
  "max_games": 10000,
  "batch_games": 20
}
```

Then run:

```bash
tunex -c configs/ironphoenix.json sprt
```

Tunex reports cumulative W/D/L, Elo estimate, LLR, boundaries, and current decision after every batch.

### 📊 SPRT model note

The first Tunex release uses a **logistic-score SPRT**. Draws count as half a win and half a loss. This is intentionally simple and useful for development, but it is not a pentanomial paired-game SPRT. The raw W/D/L and Elo output are always shown so you can inspect the actual result.

## 🎯 SPSA

SPSA is useful for tuning several search constants simultaneously.

Example parameters:

```json
"parameters": [
  {
    "name": "SearchSEEPruneMargin",
    "value": 80,
    "min": 20,
    "max": 200,
    "step": 5
  },
  {
    "name": "SearchLMRBaseX100",
    "value": 75,
    "min": 25,
    "max": 150,
    "step": 5
  }
]
```

Run:

```bash
tunex -c configs/ironphoenix.json spsa
```

Or stop at a particular total iteration:

```bash
tunex -c configs/ironphoenix.json spsa --iterations 25
```

State is written after every iteration. Restarting the same command resumes from that state file instead of throwing away the tuning run.

SPSA creates a `+` and `-` parameter vector each iteration, runs them directly against one another in paired games, estimates a gradient, and moves the center parameter vector.

## 🚀 Parallelism

`match.workers` controls simultaneous games. Each game uses **two IronPhoenix processes**, so `workers = 4` can use up to eight engine processes at once.

Start conservatively. For stable Elo testing, avoiding CPU oversubscription is more important than maximizing raw throughput.

## 🛑 Stopping a run (Ctrl+C)

Press **Ctrl+C** once in the terminal to interrupt a match, SPRT, or SPSA run. Tunex cancels games that have not started, signals active workers to stop, and closes their IronPhoenix subprocesses and redirected streams.

- **SPSA:** Each completed iteration is saved to `spsa.state_file`. Interruptions midway through an iteration do not commit a partial parameter update; rerun with the same configuration to resume from the last completed iteration.
- **Match/SPRT:** Finished game records in the JSONL results file remain on disk. Starting a new match or SPRT command does not resume the previous aggregate statistics.
- **Exit code:** Ctrl+C exits with code `130` rather than showing a Python traceback.

If IronPhoenix ignores `stop` / `quit`, Tunex force-terminates the subprocess rather than waiting indefinitely.

## 🏁 Result termination

Tunex currently ends a game when:

- IronPhoenix returns `mate 1` for the chosen root move (immediate terminal king capture),
- IronPhoenix returns `bestmove 0000`; Tunex checks whether the side is in check and records checkmate or stalemate,
- or `max_plies` is reached, which is recorded as a draw.

If IronPhoenix exits, hangs, or violates the expected protocol, the experiment fails rather than silently counting a bogus result.

## 🧪 Suggested IronPhoenix workflow

For isolated feature tests:

```text
baseline = current main/default parameter values
candidate = one changed parameter or feature
MultiPV = 1
same Hash
same time control
paired openings
same adjudication
```

For search tuning:

1. Use SPRT to establish that a search feature itself is healthy.
2. Expose its important constants as UCI spin parameters.
3. Use SPSA to tune several interacting constants.
4. Validate the final vector in a fresh long match/SPRT against the untouched baseline.

For HCE tuning, Tunex can already tune the exposed material and mobility UCI weights by games. A future offline/Texel mode can add much faster position-loss optimization without changing the match/SPSA layer.

## 📜 License

GPL-3.0-or-later. See `LICENSE`.
