# Repository Guidelines

## Project Structure & Module Organization

This workspace currently contains no application code, tests, assets, or build configuration. No Git metadata is present. Establish the layout when adding the first implementation, and update this guide to match it.

Use `src/` for application code, `tests/` for automated tests, and `docs/` for architecture notes and setup instructions when needed. Add an `assets/` directory only if the project requires static resources. Organize modules by responsibility and keep related functionality together.

## Build, Test, and Development Commands

No build, test, or local development commands are configured yet. When introducing a toolchain, document exact installation, development, build, and test commands in `README.md`. Include required runtime versions and configuration steps. Prefer repository-defined scripts so contributors and CI use the same commands.

## Coding Style & Naming Conventions

No language, formatter, or linter has been selected. Follow the standard conventions of the chosen language and add shared formatting and linting configuration with the initial implementation. Keep indentation consistent within each file. Use descriptive module, function, and variable names; avoid unexplained abbreviations. Separate unrelated changes into focused contributions.

## Testing Guidelines

No testing framework or coverage threshold is configured. Add automated tests alongside new behavior and regression tests for bug fixes. Use descriptive test names that identify the behavior and expected outcome. Keep tests deterministic and independent of live external services where practical. Document how to run the complete suite and individual tests once tooling exists.

## Commit & Pull Request Guidelines

There is no Git history from which to infer commit conventions. Use concise, imperative commit subjects, such as `Add initial project structure`. Keep commits focused. Pull requests should describe the purpose, summarize changes, link relevant issues, and report validation performed. Include screenshots for visible interface changes, and disclose any checks that could not be run.

## Security & Configuration

Never commit credentials or private data. Provide placeholder configuration examples and ignore local secrets, generated output, and dependency directories when adding the toolchain.
