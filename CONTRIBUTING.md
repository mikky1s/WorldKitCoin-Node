
# 🤝 Contributing to WorldKitCoin

First of all, thank you for considering contributing to WorldKitCoin! Any help is welcome, whether it's fixing a bug, adding a new feature, improving documentation, or just reporting an issue.

## How to contribute

### 1. Reporting bugs

If you find a bug, please open an [Issue](https://github.com/mikky1s/worldkitcoin/issues) and include:

- A clear and descriptive title
- Steps to reproduce the bug
- Expected behavior
- Actual behavior
- Your environment (OS, Python version, etc.)

### 2. Suggesting features

We welcome feature requests! Please open an [Issue](https://github.com/mikky1s/worldkitcoin/issues) with:

- A clear and descriptive title
- A detailed description of the feature
- Why it would be useful

### 3. Submitting changes (Pull Requests)

1.  **Fork the repository** and clone it locally.
2.  **Create a new branch** for your feature or fix:
    ```bash
    git checkout -b feature/your-feature-name
    ```
3. Make your changes and commit them with a clear message:

```bash
git commit -m "Add your feature description"
```
4. Push to your fork:

```bash
git push origin feature/your-feature-name
```
5. Open a Pull Request on the original repository.

Guidelines for Pull Requests
+ Code style: Follow PEP 8.

+ Tests: Make sure all tests pass. If you add a new feature, add tests for it.

+ Documentation: Update the README or other documentation if necessary.

+ One feature per PR: Keep it focused and easy to review.

Development environment
To set up a development environment:
```bash
git clone https://github.com/yourusername/worldkitcoin.git
cd worldkitcoin
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```
Run tests to make sure everything works:

```bash
python -m unittest tests.py -v
```
Code of Conduct
This project follows a Code of Conduct. By participating, you are expected to uphold this code.

Questions?
If you have any questions, feel free to open an Issue or contact us directly.

Thank you for contributing! ❤️
