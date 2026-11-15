import os
from http.server import HTTPServer, BaseHTTPRequestHandler

class HelloWorldHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Send 200 OK response
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        # The service must return exactly the text Hello, World!
        response_body = "Hello, World!".encode("utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        # Override to log to stdout/stderr or suppress default stderr logging if desired
        super().log_message(format, *args)

def run(server_class=HTTPServer, handler_class=HelloWorldHandler):
    port = int(os.environ.get("PORT", "8080"))
    server_address = ("", port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting server on port {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("Server stopped.")

if __name__ == "__main__":
    run()
