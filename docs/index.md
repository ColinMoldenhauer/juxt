<p align="center">
  <img src="assets/logo_large_transparent.png" width="160" alt="juxt logo">
</p>

# juxt

**juxt** is a fast desktop tool for visually comparing plots across multiple parameter axes. Define axes (model, date, sensor, source, …) and flip through the resulting image hypercube with keyboard navigation — fast enough to spot differences as if they were animation frames.

*juxt* comes from *juxtapose* — placing things side by side to compare.

---

## Why juxt?

When you produce plots for many parameter combinations, the most reliable way to spot visual differences is to flip through congruent images rapidly. Standard image viewers only support one-dimensional navigation (left/right), but the parameter space is N-dimensional. juxt lets you define each axis independently and navigate the full hypercube from the keyboard.

## Get started

```bash
pip install juxt
```

Then point juxt at a directory of images:

```bash
juxt /path/to/plots/
```

juxt scans the filenames, infers the axes, and opens an interactive viewer. Press letter keys to navigate axes, `Space` to toggle between two positions, and `:` to enter command mode.

See the [Installation](installation.md) page for full setup options, including the SSH extra for remote image directories.

## How it works

At startup juxt preloads all images into memory (target use case: ~100 plots, ~2–5 MB each decoded). Navigation is then instant — no per-keypress I/O. Three navigation modes are available, switchable at runtime with `:mode`.

## Pages

| Page | What's there |
|---|---|
| [UI overview](ui.md) | Annotated screenshots of the main window, status bar states, and info sidebar |
| [Installation](installation.md) | pip, extras, requirements |
| [Configuration](configuration.md) | Template mode, auto-discover, all config keys |
| [Navigation](navigation.md) | All key bindings, all three modes, command mode |
| [Remote (SSH)](remote.md) | Viewing images on a remote server over SFTP |
