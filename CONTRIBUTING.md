# Contributing to AigisCode

Thank you for your interest in contributing to AigisCode! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Getting Started

```bash
# Fork and clone the repository
git clone https://github.com/YOUR_USERNAME/aigiscode.git
cd aigiscode

# Create a virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv sync

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Verify Your Setup

```bash
# Run the CLI
aigiscode --help

# Run the test suite
python -m pytest tests/ -v
```

## Making Changes

### Workflow

1. **Fork** the repository on GitHub
2. **Create a branch** from `main` for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** following the code style guidelines below
4. **Write or update tests** for your changes
5. **Run the test suite** to make sure everything passes:
   ```bash
   python -m pytest tests/ -v
   ```
6. **Commit** with a clear message describing what and why
7. **Push** your branch and open a Pull Request

### Commit Messages

Use clear, descriptive commit messages:

- `feat: add support for Rust analysis`
- `fix: handle empty files in parser`
- `docs: update plugin development guide`
- `refactor: simplify graph traversal logic`

## Code Style Guidelines

### Type Hints

All functions and methods must include type hints. Use modern Python typing syntax:

```python
def analyze_file(path: Path, depth: int = 3) -> AnalysisResult:
    ...
```

### Pydantic Models

Use Pydantic models for data structures and configuration. Follow existing patterns in `src/aigiscode/models/`:

```python
class PluginConfig(BaseModel):
    name: str
    enabled: bool = True
    options: dict[str, Any] = {}
```

### General Guidelines

- Follow existing patterns in the codebase
- Keep functions focused and small
- Write docstrings for public APIs
- Prefer composition over inheritance
- Use `Path` objects instead of string paths

## Plugin Development

AigisCode has an extensible plugin system. If you want to create a new analyzer or reporter, see [docs/PLUGIN_SYSTEM.md](docs/PLUGIN_SYSTEM.md) for the plugin architecture and development guide.

## Reporting Bugs

Use the [Bug Report](https://github.com/aigiscode/aigiscode/issues/new?template=bug_report.md) issue template. Include:

- Steps to reproduce the issue
- Expected vs. actual behavior
- Python version and OS
- AigisCode version (`aigiscode --version`)

## Requesting Features

Use the [Feature Request](https://github.com/aigiscode/aigiscode/issues/new?template=feature_request.md) issue template. Describe the problem you are trying to solve and your proposed solution.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Questions?

Open a [Discussion](https://github.com/aigiscode/aigiscode/discussions) if you have questions about contributing or the codebase architecture.

---

Thank you for helping make AigisCode better!
