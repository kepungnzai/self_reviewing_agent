import unittest
from unittest.mock import Mock
from src.main import hello_world

class TestHelloWorld(unittest.TestCase):
    def test_hello_world(self):
        # Create a mock request object
        request = Mock()
        # Call the function
        response = hello_world(request)
        # Assert the response is exactly "Hello, World!"
        self.assertEqual(response, "Hello, World!")

if __name__ == '__main__':
    unittest.main()
