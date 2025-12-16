# Contributing to git-sync

Thank you for your interest in contributing to git-sync! This document provides guidelines for contributing.

## Development Setup

1. Clone the repository:
   ```sh
   git clone https://github.com/yourusername/git-sync.git
   cd git-sync
   ```

2. Install dependencies:
   ```sh
   pip install -e ".[dev]"
   ```

3. Run tests:
   ```sh
   pytest
   ```

## Code Style

- Use `black` for code formatting
- Use `ruff` for linting
- Use `mypy` for type checking
- Follow PEP 8 style guidelines

Run all checks:
```sh
ruff check . && black --check . && mypy .
```

## Testing

- Write tests for new features
- Ensure all tests pass before submitting PRs
- Aim for good test coverage

## Pull Requests

1. Create a feature branch from `main`
2. Make your changes
3. Run tests and linting
4. Update documentation if needed
5. Submit a PR with a clear description

## Issues

- Use GitHub issues to report bugs or request features
- Provide clear steps to reproduce bugs
- Include relevant system information