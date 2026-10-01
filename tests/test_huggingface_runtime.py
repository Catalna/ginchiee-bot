import ast
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
KEEP_AWAKE_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "keep-space-awake.yml"
)


class ZeroGPURuntimeContractTests(unittest.TestCase):
    def test_app_declares_a_zero_gpu_function(self) -> None:
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

        imported_modules = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn("spaces", imported_modules)

        gpu_functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Attribute)
                and isinstance(decorator.value, ast.Name)
                and decorator.value.id == "spaces"
                and decorator.attr == "GPU"
                for decorator in node.decorator_list
            )
        ]
        self.assertTrue(gpu_functions)

    def test_zero_gpu_function_is_registered_with_a_gradio_app(self) -> None:
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

        imported_modules = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn("gr", imported_modules)

        callback_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "click"
            and any(
                isinstance(argument, ast.Name) and argument.id == "zero_gpu_runtime_probe"
                for argument in node.args
            )
        ]
        self.assertTrue(callback_calls)

        launch_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "launch"
        ]
        self.assertTrue(launch_calls)

    def test_keep_awake_workflow_checks_the_space_every_six_hours(self) -> None:
        self.assertTrue(KEEP_AWAKE_WORKFLOW_PATH.is_file())
        workflow = KEEP_AWAKE_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("cron: '17 */6 * * *'", workflow)
        self.assertIn("https://catalna-ginchiee-bot.hf.space/", workflow)
        self.assertIn("curl --fail", workflow)


if __name__ == "__main__":
    unittest.main()
