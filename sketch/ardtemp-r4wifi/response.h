#pragma once

// Classification of the HTTP response to POST /reading.
//
// Kept free of Arduino types on purpose: the sketch includes it, and so does
// tests/response_parse_test.c, so the CI suite exercises the code that actually
// runs on the board rather than a copy that can drift away from it.

#include <string.h>
#include <stdlib.h>

// Returns:
//   -1               the service did NOT store this reading; queue and retry
//    0               stored, no command pending
//   'F','D','H','N'  stored, with that command
//
// -1 means specifically "not stored": no reply within the deadline, an
// unparseable status line, or any status outside 2xx. A 2xx is never reported
// as failure — by then the row is committed server-side, so re-queueing would
// duplicate the sample rather than recover it. That also makes a missing or
// unrecognised command a non-event rather than a retry.
//
// `len` is the number of bytes actually read, so a connection that produced no
// reply at all is distinguishable from one that did.
static inline int ardtemp_classify_response(const char *resp, int len) {
  if (len <= 0) return -1;                          // connected, never replied

  // Status line looks like "HTTP/1.1 200 OK".
  if (strncmp(resp, "HTTP/1.", 7) != 0) return -1;  // not a response we know
  const char *sp = strchr(resp, ' ');
  if (!sp) return -1;                               // malformed status line
  int status = atoi(sp + 1);
  if (status < 200 || status >= 300) return -1;     // rejected or errored

  // Accepted. Deliver any command the service piggy-backed on the response.
  // All four that loop() acts on are accepted here; a whitelist that drifts
  // from loop() is how 'H' and 'N' went undelivered.
  const char *p = strstr(resp, "\"cmd\":\"");
  if (p) {
    char c = p[7];
    if (c == 'F' || c == 'D' || c == 'H' || c == 'N') return c;
  }
  return 0;
}
