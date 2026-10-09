import os
import re
import unittest

class TestFrontend(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.web_dir = os.path.join(self.base_dir, 'control_center', 'web')
        self.index_html_path = os.path.join(self.web_dir, 'index.html')
        self.app_js_path = os.path.join(self.web_dir, 'app.js')
        self.style_css_path = os.path.join(self.web_dir, 'style.css')

    def test_files_exist(self):
        self.assertTrue(os.path.exists(self.index_html_path))
        self.assertTrue(os.path.exists(self.app_js_path))
        self.assertTrue(os.path.exists(self.style_css_path))

    def test_accessibility_and_security_in_html(self):
        with open(self.index_html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check responsive layout grid classes
        self.assertIn('grid-cols-1 lg:grid-cols-12', content, "Missing responsive grid layout")

        # Check that buttons are disabled for security
        self.assertIn('<button disabled aria-label="Approve Next Step"', content, "Approve button not disabled or missing aria-label")
        self.assertIn('<button disabled aria-label="Halt Execution"', content, "Halt button not disabled or missing aria-label")

        # Check label for attribute connects to input id
        self.assertIn('for="command-input"', content, "Label missing for attribute")
        self.assertIn('id="command-input"', content, "Input missing id attribute")
        self.assertIn('disabled', content, "Input should have disabled state")

    def test_xss_prevention_in_js(self):
        with open(self.app_js_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('function escapeHTML(str)', content, "Missing escapeHTML function")
        self.assertIn('escapeHTML(pr.title)', content, "PR title not escaped")
        self.assertIn('escapeHTML(pr.user.login)', content, "PR user not escaped")
        self.assertIn('escapeHTML(run.name)', content, "Run name not escaped")

    def test_css_styling(self):
        with open(self.style_css_path, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('.custom-scrollbar', content, "Missing custom scrollbar styling")

    def test_issue_44_components(self):
        with open(self.index_html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Agent status
        self.assertIn('Agent Status', content)
        self.assertIn('READ_ONLY', content)
        
        # Quota availability
        self.assertIn('Quota enforces ₹0 limit.', content)
        self.assertIn('₹0.00 / ₹0.00', content)
        
        # Pause/recovery
        self.assertIn('Execution Paused', content)
        self.assertIn('Awaiting manual recovery check.', content)
        
        # Handoff status
        self.assertIn('HANDOFF STATUS', content)
        self.assertIn('Pending Backend Auth', content)

if __name__ == '__main__':
    unittest.main()
