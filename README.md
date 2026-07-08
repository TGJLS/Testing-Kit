# Testing-Kit

Automated task runner for [Adaptix C2](https://github.com/Adaptix-Framework/AdaptixC2). Connects to a running Adaptix server, optionally spins up a listener and delivers an agent via SSH, then dispatches a sequence of commands and checks their output against assertions.

## Usage

```sh
git clone https://github.com/TGJLS/Testing-Kit
cd Testing-Kit
./setup.sh init   # generate SSH keys, render configs
./setup.sh up     # start Adaptix C2 + Windows containers
./setup.sh test   # run the test suite
./setup.sh down   # stop containers (./setup.sh reset to also wipe volumes)
```

## Docs

- [CLI & config reference](docs/cli.md) — all flags, `config.yaml` structure, SSH delivery, setup options
- [Tasks format](docs/tasks.md) — task fields, assertions, capture and variable substitution

## CI/CD note

Pin Docker images to digests rather than mutable tags if you incorporate this repo's workflow into your own projects.
