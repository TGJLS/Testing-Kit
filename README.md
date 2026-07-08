# Testing-Kit

Automated task runner for [Adaptix C2](https://github.com/Adaptix-Framework/AdaptixC2).

1. Creates a container running Adaptix server and a container running Windows.
2. Creates a listener and generates and delivers an agent via SSH.
3. Runs a set of commands and checks their output against.

Useful for developing [Adaptix C2](https://github.com/Adaptix-Framework/AdaptixC2) extenders and testing new features, preventing regression or CI/CD.

## Usage

```sh
git clone https://github.com/TGJLS/Testing-Kit
cd Testing-Kit
./setup.sh init   # generate SSH keys, render configs
./setup.sh up     # start Adaptix C2 + Windows containers
./setup.sh test   # run the test suite
./setup.sh down   # stop containers
./setup.sh reset  # wipe volumes
```

## Docs

+ [Tasks format](docs/tasks.md) — task fields, assertions, capture and variable substitution
+ [Deprecated Python script](python_script_deprecated/README.md)

## CI/CD note

Pin Docker images to digests rather than mutable tags if you incorporate this repo's workflow into your own projects.
