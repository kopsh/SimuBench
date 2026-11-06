# HiSLIP TLS test certificate

`test-only-cert.pem` and `test-only-key.pem` are a self-signed EC certificate for `localhost`
(valid for 100 years), made for `tests/test_hislip_tls.py` and used by nothing else. The private
key protects nothing: do not reuse it, do not trust it anywhere.

Made with:

```bash
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout test-only-key.pem -out test-only-cert.pem -days 36500 -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
```
