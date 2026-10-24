// AI Thinker ESP32-CAM: QVGA MJPEG streamer.
//   :81/stream  multipart MJPEG (encodes only while a client is connected)
//   :80/capture single JPEG      :80/health  "ok"
// The camera does no vision work: all smile detection runs on the Raspberry Pi.
#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "secrets.h"

// AI Thinker pin map
#define PWDN 32
#define RESET -1
#define XCLK 0
#define SIOD 26
#define SIOC 27
#define Y9 35
#define Y8 34
#define Y7 39
#define Y6 36
#define Y5 21
#define Y4 19
#define Y3 18
#define Y2 5
#define VSYNC 25
#define HREF 23
#define PCLK 22

static const char *BOUNDARY = "\r\n--frame\r\n";

static esp_err_t stream_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  char hdr[64];
  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) return ESP_FAIL;
    size_t n = snprintf(hdr, sizeof hdr, "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
    esp_err_t r = httpd_resp_send_chunk(req, BOUNDARY, strlen(BOUNDARY));
    if (r == ESP_OK) r = httpd_resp_send_chunk(req, hdr, n);
    if (r == ESP_OK) r = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (r != ESP_OK) return r;  // client left
  }
}

static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return httpd_resp_send_500(req);
  httpd_resp_set_type(req, "image/jpeg");
  esp_err_t r = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return r;
}

static esp_err_t health_handler(httpd_req_t *req) { return httpd_resp_send(req, "ok", 2); }

static void start_servers() {
  httpd_config_t c = HTTPD_DEFAULT_CONFIG();
  c.server_port = 80;
  httpd_uri_t cap = {"/capture", HTTP_GET, capture_handler, nullptr};
  httpd_uri_t hl = {"/health", HTTP_GET, health_handler, nullptr};
  httpd_handle_t h = nullptr;
  if (httpd_start(&h, &c) == ESP_OK) {
    httpd_register_uri_handler(h, &cap);
    httpd_register_uri_handler(h, &hl);
  }
  c.server_port = 81;
  c.ctrl_port += 1;
  httpd_uri_t st = {"/stream", HTTP_GET, stream_handler, nullptr};
  httpd_handle_t s = nullptr;
  if (httpd_start(&s, &c) == ESP_OK) httpd_register_uri_handler(s, &st);
}

void setup() {
  Serial.begin(115200);
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2; c.pin_d1 = Y3; c.pin_d2 = Y4; c.pin_d3 = Y5;
  c.pin_d4 = Y6; c.pin_d5 = Y7; c.pin_d6 = Y8; c.pin_d7 = Y9;
  c.pin_xclk = XCLK; c.pin_pclk = PCLK; c.pin_vsync = VSYNC; c.pin_href = HREF;
  c.pin_sccb_sda = SIOD; c.pin_sccb_scl = SIOC;
  c.pin_pwdn = PWDN; c.pin_reset = RESET;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_QVGA;  // 320x240: plenty for face landmarks, light on Wi-Fi
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // always the freshest frame, no stale buffer
  if (esp_camera_init(&c) != ESP_OK) {
    Serial.println("camera init failed, restarting");
    delay(2000);
    ESP.restart();
  }
  sensor_t *s = esp_camera_sensor_get();
  s->set_vflip(s, 0);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setSleep(false);  // lower latency, steadier stream
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    if (millis() - t0 > 30000) ESP.restart();
  }
  MDNS.begin(HOSTNAME);
  start_servers();
  Serial.printf("stream: http://%s:81/stream\n", WiFi.localIP().toString().c_str());
}

void loop() {
  // Safety net: reboot if Wi-Fi stays down (e.g. router restarted overnight).
  static uint32_t down = 0;
  if (WiFi.status() == WL_CONNECTED) down = 0;
  else if (!down) down = millis();
  else if (millis() - down > 60000) ESP.restart();
  delay(1000);
}
