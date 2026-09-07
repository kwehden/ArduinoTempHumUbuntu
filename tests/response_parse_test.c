/*
 * Host test for ardtemp_classify_response() in the R4 WiFi sketch.
 *
 * Includes the sketch's own header rather than restating the logic, so this
 * cannot pass while the firmware diverges. Driven by tests/test_response_parse.py.
 *
 * Response bytes below were captured from the live ardtemp-service.
 */
#include <stdio.h>
#include <string.h>
#include "../sketch/ardtemp-r4wifi/response.h"

#define R200_NULL \
  "HTTP/1.1 200 OK\r\ndate: Mon, 07 Sep 2026 19:25:44 GMT\r\nserver: uvicorn\r\n" \
  "content-length: 12\r\ncontent-type: application/json\r\nconnection: close\r\n\r\n{\"cmd\":null}"
#define R200_H \
  "HTTP/1.1 200 OK\r\ndate: Mon, 07 Sep 2026 19:25:44 GMT\r\nserver: uvicorn\r\n" \
  "content-length: 11\r\ncontent-type: application/json\r\nconnection: close\r\n\r\n{\"cmd\":\"H\"}"
#define R200_CMD(c) "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"cmd\":\"" c "\"}"
#define R422 \
  "HTTP/1.1 422 Unprocessable Entity\r\ncontent-type: application/json\r\n\r\n" \
  "{\"detail\":[{\"type\":\"json_invalid\",\"loc\":[\"body\",32],\"msg\":\"JSON decode error\"}]}"
#define R500 "HTTP/1.1 500 Internal Server Error\r\ncontent-type: text/plain\r\n\r\nInternal Server Error"
#define R404 "HTTP/1.1 404 Not Found\r\ncontent-type: text/html\r\n\r\n<html><body>404</body></html>"
#define R503 "HTTP/1.1 503 Service Unavailable\r\n\r\nno healthy upstream"
#define R301 "HTTP/1.1 301 Moved Permanently\r\nlocation: /elsewhere\r\n\r\n"
#define R204 "HTTP/1.1 204 No Content\r\n\r\n"
#define RTRUNC "HTTP/1.1 200 OK\r\ndate: Mon, 07 Sep 2026 19:25:44 GMT\r\nserver: uvicorn\r\ncontent-len"
#define RGARBAGE "\x16\x03\x01 garbage not http"

struct Case { const char *name; const char *resp; int len; int want; };

static void fmt(int v, char *b) {
  if (v == -1)     sprintf(b, "QUEUE");
  else if (v == 0) sprintf(b, "stored");
  else             sprintf(b, "'%c'", v);
}

int main(void) {
  const struct Case cases[] = {
    {"200 {\"cmd\":null} -> stored, no command", R200_NULL,     -1,  0},
    {"200 {\"cmd\":\"H\"} -> H delivered",         R200_H,        -1, 'H'},
    {"200 {\"cmd\":\"F\"} -> F delivered",         R200_CMD("F"), -1, 'F'},
    {"200 {\"cmd\":\"D\"} -> D delivered",         R200_CMD("D"), -1, 'D'},
    {"200 {\"cmd\":\"N\"} -> N delivered",         R200_CMD("N"), -1, 'N'},
    {"200 unknown command letter -> stored",     R200_CMD("X"), -1,  0},
    {"422 malformed body -> queue",              R422,          -1, -1},
    {"500 server error -> queue",                R500,          -1, -1},
    {"404 html error page -> queue",             R404,          -1, -1},
    {"503 no healthy upstream -> queue",         R503,          -1, -1},
    {"301 redirect -> queue",                    R301,          -1, -1},
    {"204 no content -> stored",                 R204,          -1,  0},
    {"200 truncated before body -> stored",      RTRUNC,        -1,  0},
    {"empty reply -> queue",                     "",             0, -1},
    {"non-HTTP garbage -> queue",                RGARBAGE,      -1, -1},
  };
  const int n = (int)(sizeof(cases) / sizeof(cases[0]));
  int failed = 0;

  for (int i = 0; i < n; i++) {
    int len = cases[i].len >= 0 ? cases[i].len : (int)strlen(cases[i].resp);
    int got = ardtemp_classify_response(cases[i].resp, len);
    if (got != cases[i].want) {
      char g[16], w[16];
      fmt(got, g); fmt(cases[i].want, w);
      printf("FAIL: %-42s got=%-7s want=%s\n", cases[i].name, g, w);
      failed++;
    }
  }
  printf("%d/%d passed\n", n - failed, n);
  return failed != 0;
}
