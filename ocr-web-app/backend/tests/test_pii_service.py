import unittest

from app.services.pii_service import privacy_boxes


class PrivacyBoxTests(unittest.TestCase):
    def test_phone_email_and_card_items_return_only_sensitive_boxes(self):
        def item(text, y):
            return {"text": text, "bbox": [[0, y], [200, y], [200, y + 10], [0, y + 10]]}

        pages = [{"page": 1, "items": [
            item("일반 문장", 0),
            item("010-1234-5678", 20),
            item("person@example.com", 40),
            item("1234-5678-9012-3456", 60),
        ]}]
        result = privacy_boxes(pages)
        self.assertEqual(len(result[0]["boxes"]), 3)


if __name__ == "__main__":
    unittest.main()
