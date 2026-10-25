// AI Thinker ESP32-CAM: QVGA MJPEG streamer.
//   :81/stream  multipart MJPEG (encodes only while a client is connected)
//   :80/capture single JPEG   :80/health "ok"
//   :80/control?preset=low|medium|high      resolution + JPEG quality
//   :80/control?led=0..255&for=SECONDS      the white flash LED (GPIO 4), PWM brightness; switches itself off after SECONDS
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

// Flash LED: the bright white LED next to the lens (shared with the SD slot, which we don't use).
#define FLASH_GPIO 4
#define FLASH_CH 2  // LEDC channel; the camera clock uses channel 0 / timer 0
static bool flash_on = false;
static uint32_t flash_off_at = 0;

static void flash_set(int level) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(FLASH_GPIO, level);
#else
  ledcWrite(FLASH_CH, level);
#endif
  flash_on = level > 0;
}

static const char *BOUNDARY = "\r\n--frame\r\n";

static esp_err_t stream_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  char hdr[64];
  // Boundary goes right AFTER each frame (not before the next one), so the client sees a frame as
  // complete the moment it is sent instead of one frame later.
  if (httpd_resp_send_chunk(req, BOUNDARY, strlen(BOUNDARY)) != ESP_OK) return ESP_FAIL;
  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) return ESP_FAIL;
    size_t n = snprintf(hdr, sizeof hdr, "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
    esp_err_t r = httpd_resp_send_chunk(req, hdr, n);
    if (r == ESP_OK) r = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    if (r == ESP_OK) r = httpd_resp_send_chunk(req, BOUNDARY, strlen(BOUNDARY));
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

// /control?preset=low|medium|high   and/or   /control?led=0..255&for=SECONDS
static esp_err_t control_handler(httpd_req_t *req) {
  const char *usage = "preset=low|medium|high and/or led=0..255&for=seconds";
  char q[96], v[16], t[12];
  if (httpd_req_get_url_query_str(req, q, sizeof q) != ESP_OK) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, usage);
  bool did = false;
  if (httpd_query_key_value(q, "preset", v, sizeof v) == ESP_OK) {
    framesize_t fs;
    int quality;
    if (!strcmp(v, "low")) { fs = FRAMESIZE_QVGA; quality = 12; }          // 320x240
    else if (!strcmp(v, "medium")) { fs = FRAMESIZE_VGA; quality = 12; }   // 640x480
    else if (!strcmp(v, "high")) { fs = FRAMESIZE_SVGA; quality = 10; }    // 800x600
    else return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, usage);
    sensor_t *s = esp_camera_sensor_get();
    s->set_framesize(s, fs);
    s->set_quality(s, quality);
    did = true;
  }
  if (httpd_query_key_value(q, "led", v, sizeof v) == ESP_OK) {
    int level = constrain(atoi(v), 0, 255);
    int secs = 180;
    if (httpd_query_key_value(q, "for", t, sizeof t) == ESP_OK) secs = constrain(atoi(t), 1, 7200);
    flash_set(level);
    flash_off_at = millis() + (uint32_t)secs * 1000UL;  // the Pi re-sends this every minute while it needs light
    did = true;
  }
  if (!did) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, usage);
  return httpd_resp_send(req, "ok", 2);
}

static esp_err_t health_handler(httpd_req_t *req) { return httpd_resp_send(req, "ok", 2); }

static void start_servers() {
  httpd_config_t c = HTTPD_DEFAULT_CONFIG();
  c.server_port = 80;
  httpd_uri_t cap = {"/capture", HTTP_GET, capture_handler, nullptr};
  httpd_uri_t hl = {"/health", HTTP_GET, health_handler, nullptr};
  httpd_uri_t ctl = {"/control", HTTP_GET, control_handler, nullptr};
  httpd_handle_t h = nullptr;
  if (httpd_start(&h, &c) == ESP_OK) {
    httpd_register_uri_handler(h, &cap);
    httpd_register_uri_handler(h, &hl);
    httpd_register_uri_handler(h, &ctl);
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

  // Flash LED PWM. After esp_camera_init, and on an explicit channel: the camera driver configures LEDC behind
  // Arduino's back, and letting Arduino pick a channel could steal the camera's clock.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttachChannel(FLASH_GPIO, 5000, 8, FLASH_CH);
#else
  ledcSetup(FLASH_CH, 5000, 8);
  ledcAttachPin(FLASH_GPIO, FLASH_CH);
#endif
  flash_set(0);

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
  // Auto-off: if the Pi crashes or loses Wi-Fi while the flash is on, the LED still goes dark.
  if (flash_on && (int32_t)(millis() - flash_off_at) >= 0) flash_set(0);
  delay(250);
}
