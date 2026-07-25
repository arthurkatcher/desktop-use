# Handoff

Operator and agent orientation for this control plane.

## Repos

| Repo | Role |
|---|---|
| [desktop-use-hosted](https://github.com/arthurkatcher/desktop-use-hosted) | Control plane (this tree): agent loop, console, dual backends, remote client |
| [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) | Data plane: Docker desktop, Desktop API, noVNC |
| [desktop-use](https://github.com/arthurkatcher/desktop-use) | Local-only reference (Xephyr on the operator machine) |

## Read next

- [README.md](README.md) for quick start, dual backends, configuration
- [AGENTS.md](AGENTS.md) for invariants and file map
- [SECURITY.md](SECURITY.md) for the single-operator threat model
- [docs/GOAL.md](docs/GOAL.md) for the screen-link goal sketch
- Longer narrative (may lag the code): [docs/HANDOFF-computer-use-stack.md](docs/HANDOFF-computer-use-stack.md)

## Version

Public MVP target: **0.0.1**.
