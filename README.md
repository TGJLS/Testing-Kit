# Testing-Kit

Automated task runner for [Adaptix C2](https://github.com/Adaptix-Framework/AdaptixC2). Connects to a running Adaptix server, optionally spins up a listener and delivers an agent via SSH, then dispatches a sequence of commands and checks their output against assertions.

## Install

```sh
uv tool install git+https://github.com/TGJLS/Testing-Kit
```

## Quick start

```sh
adaptix-testing -c config.yaml -t tasks.yaml
```

Both flags default to `config.yaml` / `tasks.yaml` in the current directory if omitted.

## Docs

- [CLI & config reference](docs/cli.md) — all flags, `config.yaml` structure, SSH delivery, setup options
- [Tasks format](docs/tasks.md) — task fields, assertions, capture and variable substitution

## CI/CD note

Pin Docker images to digests rather than mutable tags if you incorporate this repo's workflow into your own projects.
