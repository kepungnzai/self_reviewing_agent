import unittest
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer
from main import HelloWorldHandler

class TestHelloWorldServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start the server on a random free port
        cls.port = 9999
        cls.server = HTTPServer(("127.0.0.1", cls.port), HelloWorldHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()
        # Give the server a moment to start
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def test_hello_world_response(self):
        url = f"http://127.0.0.1:{self.port}/"
        try:
            with urllib.request.urlopen(url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), "text/plain; charset=utf-8")
                body = response.read().decode("utf-8")
                self.assertEqual(body, "Hello, World!")
        except urllib.error.URLError as e:
            self.fail(f"Failed to connect to local server: {e}")

if __name__ == "__main__":
    unittest.main()
