"""Policy tests for the GitHub workflows (text based, stdlib only). They fail if someone weakens the setup later."""
import pathlib, re, unittest
W = pathlib.Path(__file__).resolve().parent.parent / ".github/workflows"
read = lambda n: (W / n).read_text(encoding="utf-8")

class Policy(unittest.TestCase):
    def test_every_action_is_pinned_to_a_commit_sha(self):
        for f in W.glob("*.yml"):
            for m in re.finditer(r"uses:\s*(\S+)", f.read_text(encoding="utf-8")):
                self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", f"{f.name}: {m.group(1)} is not pinned to a full commit SHA")

    def test_no_default_permissions_and_only_ledger_can_write(self):
        for f in W.glob("*.yml"):
            t = f.read_text(encoding="utf-8")
            self.assertRegex(t, r"(?m)^permissions: \{\}\s*$", f"{f.name}: top-level permissions must be empty")
            if f.name != "ledger.yml": self.assertNotIn("write", re.sub(r"(?m)^\s*#.*$", "", t).replace("write the", ""), f"{f.name} must stay read-only")
        t = read("ledger.yml"); self.assertEqual(len(re.findall(r"contents: write", t)), 1)

    def test_ledger_runs_only_on_schedule_or_by_hand(self):
        on = re.sub(r"(?m)^\s*#.*$", "", read("ledger.yml")).split("permissions:")[0]
        for bad in ("push:", "pull_request", "pull_request_target", "workflow_run", "issue"): self.assertNotIn(bad, on)
        self.assertIn("schedule:", on); self.assertIn("workflow_dispatch:", on)

    def test_checkout_does_not_keep_the_token(self):
        for f in W.glob("*.yml"):
            t = f.read_text(encoding="utf-8"); self.assertEqual(t.count("actions/checkout@"), t.count("persist-credentials: false"), f.name)

    def test_no_untrusted_expression_inside_shell_code(self):
        for f in W.glob("*.yml"):
            for blk in re.findall(r"run:\s*[|>]?\s*\n?((?:[ ]{10,}.*\n?)+|.*)", f.read_text(encoding="utf-8")):
                for e in re.findall(r"\$\{\{([^}]*)\}\}", blk): self.fail(f"{f.name}: expression inside run: {e.strip()} (pass it through env: instead)")

    def test_token_only_in_the_push_step(self):
        t = read("ledger.yml"); self.assertEqual(t.count("github.token"), 1)
        self.assertLess(t.index("GH_TOKEN"), t.index("git add")); self.assertGreater(t.index("git add"), t.index("--bot-changes-only"))

if __name__ == "__main__": unittest.main()
