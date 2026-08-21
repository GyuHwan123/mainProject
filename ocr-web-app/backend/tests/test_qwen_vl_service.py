import unittest

from app.services.qwen_vl_service import _json_object, to_finance_schema


class QwenVLServiceTests(unittest.TestCase):
    def test_parses_fenced_model_output(self):
        self.assertEqual(_json_object({"output": "```json\n{\"가게명\": \"테스트상점\"}\n```"})["가게명"], "테스트상점")

    def test_converts_notebook_schema_to_finance_schema(self):
        result = to_finance_schema({
            "가게명": "테스트상점", "구매일자": "2026-08-21", "총 결제액": 3000,
            "카테고리": "식비", "결제방식": "카드",
            "구매물품": [{"상품명": "커피", "수량": 2, "단가": 1500, "금액": 3000}],
        })
        self.assertEqual(result["merchant"], "테스트상점")
        self.assertEqual(result["items"][0]["name"], "커피")
        self.assertEqual(result["total_amount"], 3000)


if __name__ == "__main__":
    unittest.main()
