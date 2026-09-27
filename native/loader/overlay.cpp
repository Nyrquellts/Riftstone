// Riftstone runtime: the in-game diagnostics panel ([overlay]; F10 shows and hides it).
//
// A small panel over the game, drawn at Direct3D 9's Present (live.cpp's hook) just before the frame
// goes out: the loader's version, how many enemy slots are in use and the session's peak (DDDA build
// 2364871), the address space with the loader's warning level, each plugin and whether it loaded, the
// frame rate and the stage.  A reading that is not available says UNKNOWN over an empty track: nothing
// unavailable is drawn as if it were healthy.
//
// How it draws: one managed A8R8G8B8 texture holds a glyph atlas made with GDI (Consolas and Segoe UI
// Semibold, grayscale antialiasing, printable ASCII) and a few shapes (a white texel, a disc and a ring
// for the chips, the critical droplet).  The whole panel is one DrawPrimitiveUP of pre-transformed
// vertices in its own BeginScene/EndScene, fixed-function.  The game's state is kept in a state block
// (captured before, applied after); render target 0 is the back buffer and the depth-stencil surface
// none while the panel draws, and both go back afterwards.  No D3DX and no new hook: plain Direct3D 9
// calls that DXVK implements too, whatever else (the Steam overlay) hooks Present.
//
// Hidden, the panel costs one foreground check and one GetAsyncKeyState a frame and holds no Direct3D
// object.  Its state block goes before every Reset (its texture is managed and survives one), and both
// go before the game creates another device.  An exception while drawing puts the game's state back as
// far as it can and turns the panel off for the session, with one line in loader.log.
#include "runtime.h"
#include <d3d9.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

// ---------------------------------------------------------------------------
// settings ([overlay] in riftstone_loader.ini)

enum Corner { TOP_RIGHT, TOP_LEFT, BOTTOM_RIGHT, BOTTOM_LEFT };
static const wchar_t* const CORNERS[] = {L"top-right", L"top-left", L"bottom-right", L"bottom-left"};

static BOOL g_available = FALSE;        // [overlay] enabled, and Present is hooked
static int g_vk = VK_F10;
static wchar_t g_keyName[8] = L"F10";
static int g_corner = TOP_RIGHT;
static float g_scaleSetting = 0.0f;     // 0: auto (the back buffer's height / 1080)
static BOOL g_visible = FALSE;
static BOOL g_keyHeld = TRUE;           // no toggle until the key has been seen up with the game in front
static BOOL g_noteOpen = TRUE;          // log what the panel shows once it has been open a second (each opening)
static ULONGLONG g_openAt = 0;          // when it was first drawn in this opening
static ULONGLONG g_notedAt = 0;         // at most one such line every 5 s
static volatile LONG g_dead = 0;        // an exception while drawing, or no way to draw: off for the session
static BOOL g_testFault = FALSE;        // [overlay] test_fault = 1: the harness's way into the handler below
static BOOL g_readyLogged = FALSE;
static CRITICAL_SECTION g_lock;         // Present, Reset and CreateDevice may come from different threads
static BOOL g_lockReady = FALSE;

void OverlaySettings(BOOL direct3d) {
    InitializeCriticalSection(&g_lock);
    g_lockReady = TRUE;
    if (!IniInt(L"overlay", L"enabled", 1)) {
        LogLine(L"overlay  the diagnostics panel is off ([overlay] enabled = 0)");
        return;
    }
    if (!direct3d) {
        LogLine(L"overlay  the diagnostics panel is off: [live] frame_stats = 0 leaves Direct3D alone");
        return;
    }
    wchar_t v[64];
    wchar_t* end = NULL;
    IniStr(L"overlay", L"key", L"F10", v, _countof(v));
    long n = (v[0] == L'F' || v[0] == L'f') ? wcstol(v + 1, &end, 10) : 0;
    if (end && end != v + 1 && !*end && n >= 1 && n <= 12) {
        g_vk = VK_F1 + (int)n - 1;
        _snwprintf_s(g_keyName, _countof(g_keyName), _TRUNCATE, L"F%ld", n);
    } else {
        LogLine(L"overlay  [overlay] key = %s is not one of F1..F12; F10 it is", v);
    }
    IniStr(L"overlay", L"position", L"top-right", v, _countof(v));
    g_corner = -1;
    for (int i = 0; i < 4; i++)
        if (_wcsicmp(v, CORNERS[i]) == 0) g_corner = i;
    if (g_corner < 0) {
        LogLine(L"overlay  [overlay] position = %s is not top-right, top-left, bottom-right or bottom-left; "
                L"top-right it is", v);
        g_corner = TOP_RIGHT;
    }
    IniStr(L"overlay", L"scale", L"auto", v, _countof(v));
    if (_wcsicmp(v, L"auto") != 0) {
        double d = wcstod(v, &end);
        if (end != v && !*end && d > 0) g_scaleSetting = (float)(d < 0.75 ? 0.75 : d > 3.0 ? 3.0 : d);
        else LogLine(L"overlay  [overlay] scale = %s is neither auto nor a number; auto it is", v);
    }
    g_visible = IniInt(L"overlay", L"show_at_start", 0) != 0;
    if (g_visible && SafeModeActive()) {
        g_visible = FALSE;           // safe mode starts the game as plain as it can
        LogLine(L"overlay  safe mode: the panel does not show at start; %s still shows it", g_keyName);
    }
    g_testFault = IniInt(L"overlay", L"test_fault", 0) != 0;
    g_available = TRUE;
}

void OverlayDeviceReady() {
    if (!g_available || g_readyLogged) return;
    g_readyLogged = TRUE;
    wchar_t scale[16] = L"auto";
    if (g_scaleSetting > 0) _snwprintf_s(scale, _countof(scale), _TRUNCATE, L"%.2f", g_scaleSetting);
    LogLine(L"overlay  %s shows the diagnostics panel (%s, scale %s)%s", g_keyName, CORNERS[g_corner], scale,
            g_visible ? L"; shown from the first frame" : L"");
}

// ---------------------------------------------------------------------------
// the look: the owner's design system (obsidian and slate, a chrome hairline, cyan for live values,
// ruby only for a critical state; nothing animates)

enum : uint32_t {
    C_BASE = 0x08080C, C_SURFACE = 0x12131A, C_TEXT = 0xE2E8F0, C_SECOND = 0x949DAF,
    C_LINE = 0x282D39, C_CYAN = 0x00F0FF, C_RUBY = 0xDC2626,
};
static const int A_PANEL = 235;         // the surface at 92 %
static const int A_CHROME = 166;        // the hairline at 65 %
static const int A_GLINT = 41;          // the glint on the top edge, 16 %
static const int A_PEAK = 179;          // the peak tick, 70 %
static const int A_DATA = 209;          // cyan live text, 82 %
static const int A_NOTCH = 140;         // the quiet white notch at the warning level

static DWORD Col(uint32_t rgb, int a = 255) { return ((DWORD)a << 24) | (rgb & 0xFFFFFFu); }

// The chrome reflection: stops along a 115-degree gradient across the panel.
struct Stop { float t; uint32_t rgb; };
static const Stop CHROME[] = {{0.00f, 0x303541}, {0.18f, 0x788391}, {0.29f, 0xE2E8F0}, {0.35f, 0x596372},
                              {0.56f, 0x242A35}, {0.78f, 0xA7B2C1}, {1.00f, 0x353D4A}};
static const float GRAD_DX = 0.906308f, GRAD_DY = 0.422618f;     // sin 115, -cos 115 (y down)

static uint32_t ChromeAt(float t) {
    const int n = (int)(sizeof CHROME / sizeof CHROME[0]);
    if (t <= CHROME[0].t) return CHROME[0].rgb;
    for (int i = 0; i + 1 < n; i++) {
        if (t > CHROME[i + 1].t) continue;
        float f = (t - CHROME[i].t) / (CHROME[i + 1].t - CHROME[i].t);
        uint32_t a = CHROME[i].rgb, b = CHROME[i + 1].rgb, out = 0;
        for (int sh = 0; sh <= 16; sh += 8) {
            float ca = (float)((a >> sh) & 0xFF), cb = (float)((b >> sh) & 0xFF);
            out |= (uint32_t)(ca + (cb - ca) * f + 0.5f) << sh;
        }
        return out;
    }
    return CHROME[n - 1].rgb;
}

// Layout in logical pixels at 1080p (the approved wireframe); everything is multiplied by the scale.
static const float PANEL_W = 416, PANEL_H = 256, INSET = 16, CUT_TL = 12, CUT_BR = 20;
static const float CL = 14, CR = 402;                    // content: 14 px padding on both sides
static const float HEAD_Y = 14, HEAD_H = 20, DIV1_Y = 42;
static const float EN_Y = 52, ROW_H = 18, EN_BAR_Y = 76, BAR_H = 4, TICK_H = 8;
static const float MEM_Y = 94, MEM_BAR_Y = 118, DIV2_Y = 136;
static const float CHIP_H = 22, CHIP_GAP_X = 8, CHIP_GAP_Y = 6, CHIP_PAD = 8;   // one or two rows, centred
static const float DIV3_Y = 206, FOOT_Y = 220, FOOT_H = 22;                      // between the dividers
static const float CAPTION_W = 112;                      // the column right of the meters ("peak 7")
static const float DROP_W = 8, DROP_H = 11;

// ---------------------------------------------------------------------------
// the atlas: glyphs from GDI and a few shapes, one texture

enum { F_LABEL, F_VALUE, F_HEAD, F_COUNT };              // Consolas 12, Consolas 13, Segoe UI Semibold 13
struct Glyph { uint16_t x, y, w, h; int16_t ox, adv; };  // the cell in the atlas, its left edge from the pen
struct Face { int px, ascent, cap; Glyph g[95]; };
struct Box { int x, y, w, h; };
static Face g_face[F_COUNT];
static Box g_white, g_disc, g_ring, g_drop;
static uint32_t* g_pix = NULL;                           // the atlas in memory (A8R8G8B8)
static int g_aw = 0, g_ah = 0;
static float g_pixScale = 0;                             // the scale g_pix was made for

static inline int Round(float v) { return (int)floorf(v + 0.5f); }

static void Shape(const Box& b, int kind) {
    const int N = 8;                                     // 8 x 8 samples a texel
    const float cx = b.w * 0.5f, r = b.w * 0.5f;
    // The droplet: a disc at the bottom and the cone from its tip at the top to the disc's tangents.
    const float dcy = b.h - r, tanY = dcy - r * r / dcy, tanX = r * sqrtf(1.0f - (r * r) / (dcy * dcy));
    for (int y = 0; y < b.h; y++) {
        for (int x = 0; x < b.w; x++) {
            int in = 0;
            for (int sy = 0; sy < N; sy++) {
                for (int sx = 0; sx < N; sx++) {
                    float px = x + (sx + 0.5f) / N, py = y + (sy + 0.5f) / N;
                    float dx = px - cx, dy = py - (kind == 2 ? dcy : cx), d2 = dx * dx + dy * dy;
                    BOOL inside;
                    if (kind == 0) inside = d2 <= r * r;                               // disc
                    else if (kind == 1) inside = d2 <= r * r && d2 >= (r - 1) * (r - 1);  // 1 px ring
                    else inside = d2 <= r * r || (py >= 0 && py <= tanY && fabsf(dx) <= tanX * py / tanY);
                    in += inside ? 1 : 0;
                }
            }
            uint32_t a = (uint32_t)((in * 255 + N * N / 2) / (N * N));
            g_pix[(b.y + y) * g_aw + b.x + x] = (a << 24) | 0xFFFFFFu;
        }
    }
}

static BOOL BuildAtlas(float s) {
    // Below 10 px GDI draws these faces without antialiasing (their own size rules), so 10 px is the least.
    int px[F_COUNT] = {Round(12 * s), Round(13 * s), Round(13 * s)};
    for (int& p : px)
        if (p < 10) p = 10;
    HDC dc = CreateCompatibleDC(NULL);
    if (!dc) return FALSE;
    HFONT font[F_COUNT] = {};
    BOOL ok = TRUE;
    long area = 0;
    for (int f = 0; f < F_COUNT && ok; f++) {
        font[f] = CreateFontW(-px[f], 0, 0, 0, f == F_HEAD ? FW_SEMIBOLD : FW_NORMAL, FALSE, FALSE, FALSE,
                              DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY,
                              f == F_HEAD ? VARIABLE_PITCH | FF_SWISS : FIXED_PITCH | FF_MODERN,
                              f == F_HEAD ? L"Segoe UI Semibold" : L"Consolas");
        if (!font[f]) { ok = FALSE; break; }
        HGDIOBJ old = SelectObject(dc, font[f]);
        TEXTMETRICW tm;
        GetTextMetricsW(dc, &tm);
        Face& fc = g_face[f];
        fc.px = px[f];
        fc.ascent = tm.tmAscent;
        GLYPHMETRICS gm;
        const MAT2 id = {{0, 1}, {0, 0}, {0, 0}, {0, 1}};
        fc.cap = GetGlyphOutlineW(dc, L'H', GGO_METRICS, &gm, 0, NULL, &id) != GDI_ERROR ? gm.gmptGlyphOrigin.y
                                                                                         : Round(0.7f * px[f]);
        for (int c = 32; c < 127; c++) {
            ABC abc;
            int a = 0, b = 0, adv = 0;
            if (GetCharABCWidthsW(dc, (UINT)c, (UINT)c, &abc)) {
                a = abc.abcA;
                b = (int)abc.abcB;
                adv = abc.abcA + (int)abc.abcB + abc.abcC;
            } else {
                INT w = 0;
                GetCharWidth32W(dc, (UINT)c, (UINT)c, &w);
                b = adv = w;
            }
            int left = a < 0 ? a : 0, right = a + b > adv ? a + b : adv;
            Glyph& g = fc.g[c - 32];
            g.w = (uint16_t)(right - left + 2);           // one texel of margin on each side
            g.h = (uint16_t)(tm.tmHeight + 2);
            g.ox = (int16_t)(left - 1);
            g.adv = (int16_t)adv;
            area += (long)g.w * g.h;
        }
        SelectObject(dc, old);
    }
    // The shapes: the chips' disc and ring are as tall as a chip; the droplet 8 x 11.
    const int chip = Round(CHIP_H * s);
    g_white = Box{0, 0, 4, 4};
    g_disc = Box{0, 0, chip, chip};
    g_ring = g_disc;
    g_drop = Box{0, 0, Round(DROP_W * s), Round(DROP_H * s)};
    area += 16 + 2 * (chip + 2) * (chip + 2) + (g_drop.w + 2) * (g_drop.h + 2);
    int aw = 256;
    while (ok && (long)aw * aw < area * 3 / 2 && aw < 2048) aw *= 2;
    // Shelf packing, row by row.
    int x = 0, y = 0, rowH = 0;
    auto place = [&](int w, int h, int* ox, int* oy) {
        if (x + w + 1 > aw) { x = 0; y += rowH + 1; rowH = 0; }
        *ox = x;
        *oy = y;
        x += w + 1;
        if (h > rowH) rowH = h;
    };
    int bx, by;
    place(g_white.w, g_white.h, &g_white.x, &g_white.y);
    place(g_disc.w, g_disc.h, &g_disc.x, &g_disc.y);
    place(g_ring.w, g_ring.h, &g_ring.x, &g_ring.y);
    place(g_drop.w, g_drop.h, &g_drop.x, &g_drop.y);
    for (int f = 0; f < F_COUNT; f++) {
        for (int c = 0; c < 95; c++) {
            Glyph& g = g_face[f].g[c];
            place(g.w, g.h, &bx, &by);
            g.x = (uint16_t)bx;
            g.y = (uint16_t)by;
        }
    }
    int ah = 64;
    while (ah < y + rowH + 1) ah *= 2;
    if (ah > 4096) ok = FALSE;
    // Draw the glyphs white on black; their brightness becomes the texels' alpha.
    void* bits = NULL;
    HBITMAP bmp = NULL;
    if (ok) {
        BITMAPINFO bi = {};
        bi.bmiHeader.biSize = sizeof bi.bmiHeader;
        bi.bmiHeader.biWidth = aw;
        bi.bmiHeader.biHeight = -ah;                      // top-down
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
        ok = bmp && bits;
    }
    uint32_t* pix = ok ? (uint32_t*)malloc((size_t)aw * ah * 4) : NULL;
    if (ok && pix) {
        HGDIOBJ oldBmp = SelectObject(dc, bmp);
        memset(bits, 0, (size_t)aw * ah * 4);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(255, 255, 255));
        SetTextAlign(dc, TA_TOP | TA_LEFT | TA_NOUPDATECP);
        for (int f = 0; f < F_COUNT; f++) {
            HGDIOBJ old = SelectObject(dc, font[f]);
            for (int c = 33; c < 127; c++) {
                const Glyph& g = g_face[f].g[c - 32];
                wchar_t ch = (wchar_t)c;
                TextOutW(dc, g.x - g.ox, g.y + 1, &ch, 1);
            }
            SelectObject(dc, old);
        }
        GdiFlush();
        const uint32_t* src = (const uint32_t*)bits;
        for (int i = 0; i < aw * ah; i++) {
            uint32_t p = src[i], r = (p >> 16) & 0xFF, gch = (p >> 8) & 0xFF, b = p & 0xFF;
            uint32_t a = r > gch ? r : gch;
            if (b > a) a = b;
            pix[i] = (a << 24) | 0xFFFFFFu;
        }
        SelectObject(dc, oldBmp);
        free(g_pix);
        g_pix = pix;
        g_aw = aw;
        g_ah = ah;
        for (int i = 0; i < g_white.h; i++)
            for (int j = 0; j < g_white.w; j++) g_pix[(g_white.y + i) * aw + g_white.x + j] = 0xFFFFFFFFu;
        Shape(g_disc, 0);
        Shape(g_ring, 1);
        Shape(g_drop, 2);
        g_pixScale = s;
    } else {
        free(pix);
        ok = FALSE;
    }
    if (bmp) DeleteObject(bmp);
    for (int f = 0; f < F_COUNT; f++)
        if (font[f]) DeleteObject(font[f]);
    DeleteDC(dc);
    return ok;
}

// ---------------------------------------------------------------------------
// vertices: pre-transformed, one colour and one texture coordinate each

struct Vtx { float x, y, z, rhw; DWORD c; float u, v; };
static const DWORD FVF_PANEL = D3DFVF_XYZRHW | D3DFVF_DIFFUSE | D3DFVF_TEX1;
enum { MAX_VTX = 16384 };
static Vtx g_vtx[MAX_VTX];
static int g_nv = 0;
static float g_iw, g_ih, g_wu, g_wv;                    // 1 / atlas size; the white texel's centre

// Positions are in pixel-edge coordinates (a pixel's centre is at +0.5); Direct3D 9 samples at whole
// numbers, hence the half-pixel shift, which also maps each texel onto one pixel exactly.
static inline void Put(float x, float y, DWORD c, float u, float v) {
    Vtx& o = g_vtx[g_nv++];
    o.x = x - 0.5f;
    o.y = y - 0.5f;
    o.z = 0.0f;
    o.rhw = 1.0f;
    o.c = c;
    o.u = u;
    o.v = v;
}

static void Quad(float x0, float y0, float x1, float y1, DWORD tl, DWORD tr, DWORD bl, DWORD br, float u0, float v0,
                 float u1, float v1) {
    if (g_nv + 6 > MAX_VTX) return;
    Put(x0, y0, tl, u0, v0);
    Put(x1, y0, tr, u1, v0);
    Put(x1, y1, br, u1, v1);
    Put(x0, y0, tl, u0, v0);
    Put(x1, y1, br, u1, v1);
    Put(x0, y1, bl, u0, v1);
}

static void Rect(float x0, float y0, float x1, float y1, DWORD c) {
    if (x1 > x0 && y1 > y0) Quad(x0, y0, x1, y1, c, c, c, c, g_wu, g_wv, g_wu, g_wv);
}

static void Tri(float ax, float ay, float bx, float by, float cx, float cy, DWORD c) {
    if (g_nv + 3 > MAX_VTX) return;
    Put(ax, ay, c, g_wu, g_wv);
    Put(bx, by, c, g_wu, g_wv);
    Put(cx, cy, c, g_wu, g_wv);
}

static void Poly4(float ax, float ay, DWORD ac, float bx, float by, DWORD bc, float cx, float cy, DWORD cc, float dx,
                  float dy, DWORD dc) {
    if (g_nv + 6 > MAX_VTX) return;
    Put(ax, ay, ac, g_wu, g_wv);
    Put(bx, by, bc, g_wu, g_wv);
    Put(cx, cy, cc, g_wu, g_wv);
    Put(ax, ay, ac, g_wu, g_wv);
    Put(cx, cy, cc, g_wu, g_wv);
    Put(dx, dy, dc, g_wu, g_wv);
}

// Part (sx, sy, w, h) of a shape, one texel to a pixel, at (x, y).
static void Sprite(float x, float y, const Box& b, int sx, int sy, int w, int h, DWORD c) {
    if (w <= 0 || h <= 0) return;
    Quad(x, y, x + w, y + h, c, c, c, c, (b.x + sx) * g_iw, (b.y + sy) * g_ih, (b.x + sx + w) * g_iw,
         (b.y + sy + h) * g_ih);
}

// ---------------------------------------------------------------------------
// text

static float TextW(int f, const char* s, float spacing) {
    float w = 0;
    for (const char* p = s; *p; p++) {
        int c = (unsigned char)*p;
        if (c < 32 || c > 126) c = '?';
        w += g_face[f].g[c - 32].adv + (p[1] ? spacing : 0);
    }
    return w;
}

// Draws s with its pen starting at x on the baseline; returns the pen after it.
static float Text(int f, float x, float base, const char* s, DWORD col, float spacing = 0) {
    const Face& fc = g_face[f];
    x = floorf(x + 0.5f);
    for (const char* p = s; *p; p++) {
        int c = (unsigned char)*p;
        if (c < 32 || c > 126) c = '?';
        const Glyph& g = fc.g[c - 32];
        if (c != ' ') {
            float gx = x + g.ox, gy = base - fc.ascent - 1;
            Quad(gx, gy, gx + g.w, gy + g.h, col, col, col, col, g.x * g_iw, g.y * g_ih, (g.x + g.w) * g_iw,
                 (g.y + g.h) * g_ih);
        }
        x += g.adv + (p[1] ? spacing : 0);
    }
    return x;
}

struct Run { int f; const char* s; DWORD c; float spacing; };

static float RunsW(const Run* r, int n) {
    float w = 0;
    for (int i = 0; i < n; i++) w += TextW(r[i].f, r[i].s, r[i].spacing);
    return w;
}

static void Runs(float x, float base, const Run* r, int n) {
    for (int i = 0; i < n; i++) x = Text(r[i].f, x, base, r[i].s, r[i].c, r[i].spacing);
}

static void RunsRight(float right, float base, const Run* r, int n) { Runs(right - RunsW(r, n), base, r, n); }

// ---------------------------------------------------------------------------
// the panel's geometry

static float g_s = 1.0f;                                 // the scale it is laid out at
static int g_x0, g_y0, g_w, g_h;                         // where it is on the back buffer, in pixels
static float g_gcx, g_gcy, g_glen;                       // the chrome gradient's centre and length

static inline int Px(float v) { return Round(v * g_s); }
static inline float X(float v) { return (float)(g_x0 + Px(v)); }
static inline float Y(float v) { return (float)(g_y0 + Px(v)); }

// The scale for this back buffer (0: too small for the panel).
static float ScaleFor(UINT bw, UINT bh) {
    float s = g_scaleSetting > 0 ? g_scaleSetting : (float)bh / 1080.0f;
    if (s < 0.75f) s = 0.75f;
    if (s > 3.0f) s = 3.0f;
    // A back buffer too small for the panel at that size gets a smaller one (down to half size).
    float fit = (float)bw / (PANEL_W + 2 * INSET), fitH = (float)bh / (PANEL_H + 2 * INSET);
    if (fitH < fit) fit = fitH;
    if (s > fit) s = fit;
    return s >= 0.5f ? s : 0.0f;
}

static void Place(UINT bw, UINT bh, float s) {
    g_s = s;
    g_w = Px(PANEL_W);
    g_h = Px(PANEL_H);
    int inset = Px(INSET);
    g_x0 = g_corner == TOP_LEFT || g_corner == BOTTOM_LEFT ? inset : (int)bw - inset - g_w;
    g_y0 = g_corner == TOP_LEFT || g_corner == TOP_RIGHT ? inset : (int)bh - inset - g_h;
    g_gcx = g_x0 + g_w * 0.5f;
    g_gcy = g_y0 + g_h * 0.5f;
    g_glen = g_w * GRAD_DX + g_h * GRAD_DY;
}

static DWORD ChromeCol(float x, float y) {
    float t = ((x - g_gcx) * GRAD_DX + (y - g_gcy) * GRAD_DY) / g_glen + 0.5f;
    return Col(ChromeAt(t), A_CHROME);
}

static int Step() { return Px(8) > 4 ? Px(8) : 4; }     // the hairline's colour is set every 8 px

// The hairline: pixel columns [xa, xb) of row y; column x over rows [ya, yb); and the 45-degree
// cuts, one pixel a row, the band [left, left + 1) moving one column left a row from `left` at ya.
static void HLine(int xa, int xb, int y) {
    for (int a = xa; a < xb; a += Step()) {
        int b = a + Step() < xb ? a + Step() : xb;
        DWORD ca = ChromeCol((float)a, y + 0.5f), cb = ChromeCol((float)b, y + 0.5f);
        Quad((float)a, (float)y, (float)b, (float)y + 1, ca, cb, ca, cb, g_wu, g_wv, g_wu, g_wv);
    }
}

static void VLine(int x, int ya, int yb) {
    for (int a = ya; a < yb; a += Step()) {
        int b = a + Step() < yb ? a + Step() : yb;
        DWORD ca = ChromeCol(x + 0.5f, (float)a), cb = ChromeCol(x + 0.5f, (float)b);
        Quad((float)x, (float)a, (float)x + 1, (float)b, ca, ca, cb, cb, g_wu, g_wv, g_wu, g_wv);
    }
}

static void Diagonal(float left, int ya, int yb) {
    for (int a = ya; a < yb; a += Step()) {
        int b = a + Step() < yb ? a + Step() : yb;
        float la = left - (a - ya), lb = left - (b - ya);
        DWORD ca = ChromeCol(la + 0.5f, (float)a), cb = ChromeCol(lb + 0.5f, (float)b);
        Poly4(la, (float)a, ca, la + 1, (float)a, ca, lb + 1, (float)b, cb, lb, (float)b, cb);
    }
}

// The cap-centred baseline for text of face f in the row [top, top + h) (logical pixels).
static float Base(float top, float h, int f) { return (float)(g_y0 + Px(top) + (Px(h) + g_face[f].cap + 1) / 2); }

static float Spacing() { return (float)(Round(g_s) > 1 ? Round(g_s) : 1); }   // labels: about 0.08 em

static void Divider(float y) { Rect(X(CL), Y(y), X(CR), Y(y) + 1, Col(C_LINE)); }

// A meter: the neutral track, and the fill when the reading is known.
static void Meter(float x, float y, float w, float h, float frac, DWORD fill) {
    Rect(x, y, x + w, y + h, Col(C_LINE));
    if (frac > 0) {
        float fw = floorf(w * (frac > 1 ? 1 : frac) + 0.5f);
        Rect(x, y, x + fw, y + h, fill);
    }
}

// A 1 px tick at frac of the meter, th tall, centred on it.
static void Tick(float x, float y, float w, float h, float frac, float th, DWORD c) {
    float tx = x + floorf(w * (frac > 1 ? 1 : frac < 0 ? 0 : frac) + 0.5f);
    if (tx > x + w - 1) tx = x + w - 1;
    float top = y - floorf((th - h) / 2);
    Rect(tx, top, tx + 1, top + th, c);
}

// A chip: a slate pill with a 1 px border; `accent` colours the border's left end (ruby when critical).
static void Chip(float x, float y, float w, DWORD border, DWORD accent) {
    int d = g_disc.w, hl = d / 2, hr = d - hl;
    DWORD fill = Col(C_SURFACE);
    Sprite(x, y, g_disc, 0, 0, hl, d, fill);
    Rect(x + hl, y, x + w - hr, y + d, fill);
    Sprite(x + w - hr, y, g_disc, hl, 0, hr, d, fill);
    Sprite(x, y, g_ring, 0, 0, hl, d, accent);
    Rect(x + hl, y, x + w - hr, y + 1, border);
    Rect(x + hl, y + d - 1, x + w - hr, y + d, border);
    Sprite(x + w - hr, y, g_ring, hl, 0, hr, d, border);
}

// ---------------------------------------------------------------------------
// the readings, refreshed four times a second while the panel shows

struct Readings {
    int active, slots, peak;             // enemy slots (-1 unknown)
    BOOL mem, low;                       // address space known; the loader's warning
    uint64_t used, total;
    uint32_t frameUs;                    // average frame time (0 unknown)
    int stage;                           // -1 unknown
};
static Readings g_read;
static ULONGLONG g_readAt = 0;

static void Refresh() {
    ULONGLONG now = GetTickCount64();
    if (g_readAt && now - g_readAt < 250) return;
    g_readAt = now;
    Readings r = {};
    int usable = -1;
    r.active = r.slots = -1;
    if (EnemySlots(&r.active, &usable, &r.slots) && r.slots > 0 && r.active >= 0) NoteEnemyPeak(r.active);
    else r.active = r.slots = -1;
    r.peak = r.slots > 0 ? EnemyPeak() : -1;
    MemInfo m;
    if (!LatestMemory(&m)) SampleMemory(&m, FALSE);        // no live thread: this sample, without the walk
    r.mem = m.vaTotal != 0 && m.vaUsed <= m.vaTotal;
    r.used = m.vaUsed;
    r.total = m.vaTotal;
    r.low = r.mem && AddressSpaceLow(&m);
    r.frameUs = RecentFrameAverageUs(64);
    int stage = -1;
    r.stage = CurrentStage(&stage) ? stage : -1;
    g_read = r;
}

// The chips: safe mode first when it is on, then each plugin in load order.
struct ChipInfo { char name[24]; const char* state; int kind; };   // kind 0 live, 1 critical, 2 neutral, 3 count
static ChipInfo g_chipInfo[MAX_PLUGINS + 2];
static int g_chipCount = -1;

static void CollectChips() {
    int n = 0;
    if (SafeModeActive()) g_chipInfo[n++] = ChipInfo{"safe mode", "ON", 1};
    for (int i = 0; i < g_pluginCount && n < MAX_PLUGINS + 1; i++) {
        const PluginInfo& pi = g_pluginInfo[i];
        ChipInfo& c = g_chipInfo[n++];
        const wchar_t* dot = wcsrchr(pi.name, L'.');
        size_t len = dot ? (size_t)(dot - pi.name) : wcslen(pi.name), k = 0;
        for (; k < len && k < sizeof c.name - 1; k++)
            c.name[k] = pi.name[k] >= 32 && pi.name[k] < 127 ? (char)pi.name[k] : '?';
        c.name[k] = 0;
        if (len > 20) strcpy_s(c.name + 19, sizeof c.name - 19, "~");   // long names keep 19 characters
        c.state = pi.state == 1 ? "ACTIVE" : pi.state == 0 ? "FAILED" : "SKIPPED";
        c.kind = pi.state == 1 ? 0 : 1;
    }
    if (!g_pluginCount) g_chipInfo[n++] = ChipInfo{"plugins", "NONE", 2};
    g_chipCount = n;
}

// "name: STATE"; kind 3 is the count of chips that did not fit, "+N more".
enum { CHIP_COUNT = 3 };

static float ChipW(const char* name, const char* state, int kind) {
    float w = TextW(F_LABEL, name, 0) + TextW(F_LABEL, kind == CHIP_COUNT ? " " : ": ", 0) + TextW(F_LABEL, state, 0) +
              2 * Px(CHIP_PAD);
    return w > g_disc.w ? w : (float)g_disc.w;
}

static void DrawChip(float x, float y, const char* name, const char* state, int kind) {
    float w = ChipW(name, state, kind);
    Chip(x, y, w, Col(C_LINE), kind == 1 ? Col(C_RUBY) : Col(C_LINE));
    DWORD value = kind == 0 ? Col(C_CYAN, A_DATA) : kind == 1 ? Col(C_TEXT) : Col(C_SECOND);
    Run r[] = {{F_LABEL, name, Col(C_SECOND), 0}, {F_LABEL, kind == CHIP_COUNT ? " " : ": ", Col(C_SECOND), 0},
               {F_LABEL, state, value, 0}};
    Runs(x + Px(CHIP_PAD), y + (g_disc.w + g_face[F_LABEL].cap + 1) / 2, r, 3);
}

// Up to two rows of chips, centred between their dividers; when they do not all fit, the second row
// ends with "+N more".
static void DrawChips() {
    if (g_chipCount < 0) CollectChips();
    const float left = X(CL), right = X(CR), gap = (float)Px(CHIP_GAP_X);
    float cx[MAX_PLUGINS + 2], cw[MAX_PLUGINS + 2];
    int crow[MAX_PLUGINS + 2], placed = 0, row = 0;
    float x = left;
    for (int i = 0; i < g_chipCount; i++) {
        float w = ChipW(g_chipInfo[i].name, g_chipInfo[i].state, g_chipInfo[i].kind);
        if (x > left && x + w > right) {
            row++;
            x = left;
        }
        if (row > 1) break;
        cx[i] = x;
        cw[i] = w;
        crow[i] = row;
        x += w + gap;
        placed = i + 1;
    }
    int shown = placed, moreRow = -1;
    float moreX = left;
    char more[16] = "";
    if (placed < g_chipCount) {
        // The second row keeps as many chips as leave room for the count after them (the first chip is
        // always on the first row, so this ends by shown == 1).
        for (shown = placed; shown > 0; shown--) {
            _snprintf_s(more, _countof(more), _TRUNCATE, "+%d", g_chipCount - shown);
            moreRow = 1;
            if (crow[shown - 1] == 0) break;                 // the second row is empty now: the count starts it
            moreX = cx[shown - 1] + cw[shown - 1] + gap;
            if (moreX + ChipW(more, "more", CHIP_COUNT) <= right) break;
            moreX = left;
        }
    }
    int rows = moreRow == 1 || (shown && crow[shown - 1] == 1) ? 2 : 1;
    float block = (float)(rows * g_disc.w + (rows - 1) * Px(CHIP_GAP_Y));
    float top = floorf((Y(DIV2_Y) + 1 + Y(DIV3_Y) - block) / 2);
    const float rowY[2] = {top, top + g_disc.w + Px(CHIP_GAP_Y)};
    for (int i = 0; i < shown; i++)
        DrawChip(cx[i], rowY[crow[i]], g_chipInfo[i].name, g_chipInfo[i].state, g_chipInfo[i].kind);
    if (moreRow >= 0) DrawChip(moreX, rowY[moreRow], more, "more", CHIP_COUNT);
}

// ---------------------------------------------------------------------------
// the panel

static void DrawShape() {
    int x0 = g_x0, y0 = g_y0, x1 = g_x0 + g_w, y1 = g_y0 + g_h, c1 = Px(CUT_TL), c2 = Px(CUT_BR);
    // The surface: the rectangle with its upper-left and lower-right corners cut at 45 degrees.  The
    // cuts sit half a pixel off the pixel centres, so every pixel is plainly in or out.
    DWORD fill = Col(C_BASE, A_PANEL);
    float ax = x0 + c1 - 0.5f, ay = (float)y0;              // the top edge after the upper-left cut
    Tri(ax, ay, (float)x1, (float)y0, (float)x1, y1 - c2 + 0.5f, fill);
    Tri(ax, ay, (float)x1, y1 - c2 + 0.5f, x1 - c2 + 0.5f, (float)y1, fill);
    Tri(ax, ay, x1 - c2 + 0.5f, (float)y1, (float)x0, (float)y1, fill);
    Tri(ax, ay, (float)x0, (float)y1, (float)x0, y0 + c1 - 0.5f, fill);
    // The chrome hairline, one pixel inside that outline, each pixel drawn once.
    HLine(x0 + c1, x1 - 1, y0);
    VLine(x1 - 1, y0, y1 - c2);
    Diagonal(x1 - 0.5f, y1 - c2, y1);
    HLine(x0 + 1, x1 - c2, y1 - 1);
    VLine(x0, y0 + c1, y1);
    Diagonal(x0 + c1 - 0.5f, y0, y0 + c1);
    // A short cyan glint on the top edge.
    float ga = X(28), gb = X(104), ramp = (float)Px(12);
    DWORD g0 = Col(C_CYAN, 0), g1 = Col(C_CYAN, A_GLINT);
    Quad(ga, (float)y0, ga + ramp, (float)y0 + 1, g0, g1, g0, g1, g_wu, g_wv, g_wu, g_wv);
    Rect(ga + ramp, (float)y0, gb - ramp, (float)y0 + 1, g1);
    Quad(gb - ramp, (float)y0, gb, (float)y0 + 1, g1, g0, g1, g0, g_wu, g_wv, g_wu, g_wv);
}

static int BuildPanel() {
    g_nv = 0;
    const Readings& r = g_read;
    const float sp = Spacing();
    const DWORD second = Col(C_SECOND), text = Col(C_TEXT), live = Col(C_CYAN, A_DATA);
    char a[32], b[32], c[64];
    DrawShape();

    // NryQ // Riftstone v<RIFTSTONE_VERSION_A, runtime.h>                          F10
    _snprintf_s(c, _countof(c), _TRUNCATE, "NryQ // Riftstone v%s", RIFTSTONE_VERSION_A);
    Text(F_HEAD, X(CL), Base(HEAD_Y, HEAD_H, F_HEAD), c, text);
    char key[8];
    WideCharToMultiByte(CP_ACP, 0, g_keyName, -1, key, sizeof key, NULL, NULL);
    Text(F_LABEL, X(CR) - TextW(F_LABEL, key, sp), Base(HEAD_Y, HEAD_H, F_LABEL), key, second, sp);
    Divider(DIV1_Y);

    // ACTIVE ENEMY POOL                                                 3 / 10 slots
    // [meter: in use / slots, a tick at the session's peak]                 peak 7
    const float mx = X(CL), mw = X(CR - CAPTION_W) - X(CL), mh = (float)Px(BAR_H);
    Text(F_LABEL, X(CL), Base(EN_Y, ROW_H, F_LABEL), "ACTIVE ENEMY POOL", second, sp);
    float my = Y(EN_BAR_Y), capBase = my + (float)((Px(BAR_H) + g_face[F_LABEL].cap + 1) / 2);
    if (r.slots > 0) {
        _snprintf_s(a, _countof(a), _TRUNCATE, "%d", r.active);
        _snprintf_s(b, _countof(b), _TRUNCATE, "%d", r.slots);
        Run v[] = {{F_VALUE, a, live, 0}, {F_VALUE, " / ", second, 0}, {F_VALUE, b, text, 0},
                   {F_VALUE, " slots", second, 0}};
        RunsRight(X(CR), Base(EN_Y, ROW_H, F_VALUE), v, 4);
        Meter(mx, my, mw, mh, (float)r.active / r.slots, Col(C_CYAN));
        if (r.peak >= 0) {
            Tick(mx, my, mw, mh, (float)r.peak / r.slots, (float)Px(TICK_H), Col(C_CYAN, A_PEAK));
            _snprintf_s(a, _countof(a), _TRUNCATE, "%d", r.peak);
            Run p[] = {{F_LABEL, "peak ", second, 0}, {F_LABEL, a, live, 0}};
            RunsRight(X(CR), capBase, p, 2);
        }
    } else {
        Run v[] = {{F_VALUE, "UNKNOWN", second, 0}};
        RunsRight(X(CR), Base(EN_Y, ROW_H, F_VALUE), v, 1);
        Meter(mx, my, mw, mh, 0, 0);
    }

    // MEMORY GUARD                                                  2.71 / 4.00 GB
    // [meter: address space used / total, a notch at the warning level]   32.2% headroom
    my = Y(MEM_BAR_Y);
    capBase = my + (float)((Px(BAR_H) + g_face[F_LABEL].cap + 1) / 2);
    const float labelBase = Base(MEM_Y, ROW_H, F_LABEL);
    if (r.low) {
        // Critical: a ruby droplet, the reason in white, the meter in ruby, a ruby mark on the border.
        float dy = Y(MEM_Y) + floorf((Px(ROW_H) - g_drop.h) / 2.0f);
        Sprite(X(CL), dy, g_drop, 0, 0, g_drop.w, g_drop.h, Col(C_RUBY));
        Text(F_LABEL, X(CL) + g_drop.w + Px(6), labelBase, "Address space nearly used up", text);
        Rect((float)g_x0, Y(MEM_Y), (float)g_x0 + 2, my + mh, Col(C_RUBY));
    } else {
        Text(F_LABEL, X(CL), labelBase, "MEMORY GUARD", second, sp);
    }
    if (r.mem) {
        const double gb = 1024.0 * 1024.0 * 1024.0, warn = 400.0 * 1024 * 1024;
        _snprintf_s(a, _countof(a), _TRUNCATE, "%.2f", r.used / gb);
        _snprintf_s(b, _countof(b), _TRUNCATE, "%.2f", r.total / gb);
        Run v[] = {{F_VALUE, a, live, 0}, {F_VALUE, " / ", second, 0}, {F_VALUE, b, text, 0}, {F_VALUE, " GB", second, 0}};
        RunsRight(X(CR), Base(MEM_Y, ROW_H, F_VALUE), v, 4);
        Meter(mx, my, mw, mh, (float)((double)r.used / r.total), r.low ? Col(C_RUBY) : Col(C_CYAN));
        if (r.total > (uint64_t)warn)
            Tick(mx, my, mw, mh, (float)((r.total - warn) / r.total), (float)Px(TICK_H), Col(C_TEXT, A_NOTCH));
        _snprintf_s(a, _countof(a), _TRUNCATE, "%.1f%%", 100.0 * (double)(r.total - r.used) / r.total);
        Run h[] = {{F_LABEL, a, live, 0}, {F_LABEL, " headroom", second, 0}};
        RunsRight(X(CR), capBase, h, 2);
    } else {
        Run v[] = {{F_VALUE, "UNKNOWN", second, 0}};
        RunsRight(X(CR), Base(MEM_Y, ROW_H, F_VALUE), v, 1);
        Meter(mx, my, mw, mh, 0, 0);
    }
    Divider(DIV2_Y);

    // [plugin: ACTIVE] [plugin: FAILED] ...
    DrawChips();
    Divider(DIV3_Y);

    // 60.0 FPS                                                             STAGE 100
    const float fb = Base(FOOT_Y, FOOT_H, F_VALUE);
    if (r.frameUs) {
        _snprintf_s(c, _countof(c), _TRUNCATE, "%.1f FPS", 1000000.0 / r.frameUs);
        Text(F_VALUE, X(CL), fb, c, live);
    } else {
        Text(F_VALUE, X(CL), fb, "FPS UNKNOWN", second);
    }
    if (r.stage >= 0) _snprintf_s(a, _countof(a), _TRUNCATE, "%d", r.stage);
    else strcpy_s(a, "UNKNOWN");
    float nw = TextW(F_VALUE, a, 0), lw = TextW(F_LABEL, "STAGE", sp), gap = (float)Px(6);
    Text(F_LABEL, X(CR) - nw - gap - lw, Base(FOOT_Y, FOOT_H, F_LABEL), "STAGE", second, sp);
    Text(F_VALUE, X(CR) - nw, fb, a, r.stage >= 0 ? text : second);
    return g_nv;
}

// ---------------------------------------------------------------------------
// Direct3D: the texture, the state block, and drawing with the game's state kept

static IDirect3DDevice9* g_dev = NULL;                   // the device the objects below belong to
static IDirect3DTexture9* g_tex = NULL;                  // the atlas (managed: survives a Reset)
static float g_texScale = 0;
static IDirect3DStateBlock9* g_sb = NULL;                // the game's state while the panel draws
static DWORD g_maxRt = 1;
static BOOL g_devChecked = FALSE, g_devUsable = TRUE;
static float g_loggedScale = 0;
static UINT g_loggedW = 0, g_loggedH = 0;

// What one draw took from the game, to put back (also from the exception handler).
static IDirect3DSurface9* s_bb = NULL;
static IDirect3DSurface9* s_rt[4] = {};
static IDirect3DSurface9* s_ds = NULL;
static BOOL s_captured = FALSE, s_inScene = FALSE, s_bound = FALSE;

static void ReleaseObjects() {
    if (g_sb) g_sb->Release();
    if (g_tex) g_tex->Release();
    g_sb = NULL;
    g_tex = NULL;
    g_texScale = 0;
}

static void Off(const wchar_t* why, HRESULT hr = S_OK) {
    InterlockedExchange(&g_dead, 1);
    ReleaseObjects();                                        // off holds nothing (nor keeps the device alive)
    wchar_t code[24] = L"";
    if (hr != S_OK) _snwprintf_s(code, _countof(code), _TRUNCATE, L" (0x%08lx)", (unsigned long)hr);
    LogLine(L"overlay  %s%s; the diagnostics panel stays off for this session", why, code);
}

static BOOL Ready(IDirect3DDevice9* dev, float s) {
    if (dev != g_dev) {
        ReleaseObjects();
        g_dev = dev;
        g_devChecked = FALSE;
    }
    if (!g_devChecked) {
        g_devChecked = TRUE;
        g_devUsable = TRUE;
        D3DDEVICE_CREATION_PARAMETERS cp;
        if (SUCCEEDED(dev->GetCreationParameters(&cp)) && (cp.BehaviorFlags & D3DCREATE_PUREDEVICE)) {
            // A pure device keeps no copy of its state, so a state block could not put the game's back.
            Off(L"the game's Direct3D device is a pure device: its drawing state cannot be saved");
            g_devUsable = FALSE;
        }
        D3DCAPS9 caps;
        g_maxRt = SUCCEEDED(dev->GetDeviceCaps(&caps)) && caps.NumSimultaneousRTs > 1
                      ? (caps.NumSimultaneousRTs < 4 ? caps.NumSimultaneousRTs : 4) : 1;
    }
    if (!g_devUsable) return FALSE;
    if (g_pixScale != s && !BuildAtlas(s)) {
        Off(L"the panel's glyphs could not be made with GDI");
        return FALSE;
    }
    if (!g_tex || g_texScale != s) {
        if (g_tex) g_tex->Release();
        g_tex = NULL;
        HRESULT hr = dev->CreateTexture((UINT)g_aw, (UINT)g_ah, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &g_tex, NULL);
        if (FAILED(hr) || !g_tex) {
            g_tex = NULL;
            Off(L"the panel's texture could not be made", hr);
            return FALSE;
        }
        D3DLOCKED_RECT lr;
        hr = g_tex->LockRect(0, &lr, NULL, 0);
        if (FAILED(hr)) {
            Off(L"the panel's texture could not be filled", hr);
            return FALSE;
        }
        for (int y = 0; y < g_ah; y++) memcpy((BYTE*)lr.pBits + (size_t)y * lr.Pitch, g_pix + (size_t)y * g_aw, (size_t)g_aw * 4);
        g_tex->UnlockRect(0);
        g_texScale = s;
        g_iw = 1.0f / g_aw;
        g_ih = 1.0f / g_ah;
        g_wu = (g_white.x + 1.5f) * g_iw;
        g_wv = (g_white.y + 1.5f) * g_ih;
    }
    if (!g_sb) {
        HRESULT hr = dev->CreateStateBlock(D3DSBT_ALL, &g_sb);
        if (FAILED(hr) || !g_sb) {
            g_sb = NULL;
            Off(L"the game's drawing state could not be saved (CreateStateBlock)", hr);
            return FALSE;
        }
    }
    return TRUE;
}

// Everything the panel relies on, set explicitly: whatever the game left bound stays out of it.
static void SetDrawState(IDirect3DDevice9* dev, UINT w, UINT h) {
    static const DWORD rs[][2] = {
        {D3DRS_ZENABLE, D3DZB_FALSE}, {D3DRS_ZWRITEENABLE, FALSE}, {D3DRS_FILLMODE, D3DFILL_SOLID},
        {D3DRS_SHADEMODE, D3DSHADE_GOURAUD}, {D3DRS_ALPHATESTENABLE, FALSE}, {D3DRS_CULLMODE, D3DCULL_NONE},
        {D3DRS_ALPHABLENDENABLE, TRUE}, {D3DRS_SRCBLEND, D3DBLEND_SRCALPHA}, {D3DRS_DESTBLEND, D3DBLEND_INVSRCALPHA},
        {D3DRS_BLENDOP, D3DBLENDOP_ADD}, {D3DRS_SEPARATEALPHABLENDENABLE, FALSE}, {D3DRS_FOGENABLE, FALSE},
        {D3DRS_SPECULARENABLE, FALSE}, {D3DRS_LIGHTING, FALSE}, {D3DRS_STENCILENABLE, FALSE},
        {D3DRS_TWOSIDEDSTENCILMODE, FALSE}, {D3DRS_SCISSORTESTENABLE, FALSE}, {D3DRS_CLIPPING, TRUE},
        {D3DRS_CLIPPLANEENABLE, 0}, {D3DRS_COLORWRITEENABLE, 0xF}, {D3DRS_SRGBWRITEENABLE, FALSE},
        {D3DRS_WRAP0, 0}, {D3DRS_DITHERENABLE, FALSE}, {D3DRS_MULTISAMPLEANTIALIAS, FALSE},
        {D3DRS_MULTISAMPLEMASK, 0xFFFFFFFF}, {D3DRS_VERTEXBLEND, D3DVBF_DISABLE},
        {D3DRS_INDEXEDVERTEXBLENDENABLE, FALSE}, {D3DRS_ANTIALIASEDLINEENABLE, FALSE},
    };
    static const DWORD stage0[][2] = {
        {D3DTSS_COLOROP, D3DTOP_MODULATE}, {D3DTSS_COLORARG1, D3DTA_TEXTURE}, {D3DTSS_COLORARG2, D3DTA_DIFFUSE},
        {D3DTSS_ALPHAOP, D3DTOP_MODULATE}, {D3DTSS_ALPHAARG1, D3DTA_TEXTURE}, {D3DTSS_ALPHAARG2, D3DTA_DIFFUSE},
        {D3DTSS_TEXCOORDINDEX, 0}, {D3DTSS_TEXTURETRANSFORMFLAGS, D3DTTFF_DISABLE}, {D3DTSS_RESULTARG, D3DTA_CURRENT},
    };
    static const DWORD samp[][2] = {
        {D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP}, {D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP}, {D3DSAMP_ADDRESSW, D3DTADDRESS_CLAMP},
        {D3DSAMP_MAGFILTER, D3DTEXF_POINT}, {D3DSAMP_MINFILTER, D3DTEXF_POINT}, {D3DSAMP_MIPFILTER, D3DTEXF_NONE},
        {D3DSAMP_MAXMIPLEVEL, 0}, {D3DSAMP_MIPMAPLODBIAS, 0}, {D3DSAMP_MAXANISOTROPY, 1}, {D3DSAMP_SRGBTEXTURE, 0},
    };
    dev->SetVertexShader(NULL);
    dev->SetPixelShader(NULL);
    dev->SetFVF(FVF_PANEL);
    dev->SetTexture(0, g_tex);
    for (const auto& s : rs) dev->SetRenderState((D3DRENDERSTATETYPE)s[0], s[1]);
    for (const auto& s : stage0) dev->SetTextureStageState(0, (D3DTEXTURESTAGESTATETYPE)s[0], s[1]);
    dev->SetTextureStageState(1, D3DTSS_COLOROP, D3DTOP_DISABLE);
    dev->SetTextureStageState(1, D3DTSS_ALPHAOP, D3DTOP_DISABLE);
    for (const auto& s : samp) dev->SetSamplerState(0, (D3DSAMPLERSTATETYPE)s[0], s[1]);
    D3DVIEWPORT9 vp = {0, 0, w, h, 0.0f, 1.0f};
    dev->SetViewport(&vp);
}

// Puts back what the draw took and lets go of the references it held.
static void Restore(IDirect3DDevice9* dev) {
    if (s_inScene) {
        dev->EndScene();
        s_inScene = FALSE;
    }
    if (s_bound) {
        if (s_rt[0]) dev->SetRenderTarget(0, s_rt[0]);   // resets the viewport, which the state block restores
        for (DWORD i = 1; i < g_maxRt; i++)
            if (s_rt[i]) dev->SetRenderTarget(i, s_rt[i]);
        dev->SetDepthStencilSurface(s_ds);
        s_bound = FALSE;
    }
    if (s_captured) {
        g_sb->Apply();
        s_captured = FALSE;
    }
    for (int i = 0; i < 4; i++) {
        if (s_rt[i]) s_rt[i]->Release();
        s_rt[i] = NULL;
    }
    if (s_ds) s_ds->Release();
    if (s_bb) s_bb->Release();
    s_ds = s_bb = NULL;
}

// What the panel showed once it had been open a second (its readings refresh four times a second and the
// frame rate needs frames), in loader.log, so a session can be checked afterwards against the plugins' own
// records (enemy_cap.log's slot counts) and the exit summary.  Once per opening, at most every 5 s.
static void NoteReadings() {
    ULONGLONG now = GetTickCount64();
    if (!g_noteOpen) return;
    if (!g_openAt) g_openAt = now;
    if (now - g_openAt < 1000 || (g_notedAt && now - g_notedAt < 5000)) return;
    g_noteOpen = FALSE;
    g_notedAt = now;
    const Readings& r = g_read;
    wchar_t pool[64], mem[80], fps[32], stage[24];
    if (r.slots > 0) _snwprintf_s(pool, _countof(pool), _TRUNCATE, L"enemy pool %d / %d slots (peak %d)", r.active,
                                  r.slots, r.peak);
    else wcscpy_s(pool, L"enemy pool UNKNOWN");
    const double gb = 1024.0 * 1024.0 * 1024.0;
    if (r.mem) _snwprintf_s(mem, _countof(mem), _TRUNCATE, L"address space %.2f / %.2f GB (%.1f%% headroom)%s",
                            r.used / gb, r.total / gb, 100.0 * (double)(r.total - r.used) / r.total,
                            r.low ? L", nearly used up" : L"");
    else wcscpy_s(mem, L"address space UNKNOWN");
    if (r.frameUs) _snwprintf_s(fps, _countof(fps), _TRUNCATE, L"%.1f fps", 1000000.0 / r.frameUs);
    else wcscpy_s(fps, L"fps UNKNOWN");
    if (r.stage >= 0) _snwprintf_s(stage, _countof(stage), _TRUNCATE, L"stage %d", r.stage);
    else wcscpy_s(stage, L"stage UNKNOWN");
    LogLine(L"overlay  panel opened: %s, %s, %s, %s", pool, mem, fps, stage);
}

static void DrawUnsafe(IDirect3DDevice9* dev) {
    if (dev->TestCooperativeLevel() != D3D_OK) return;   // a lost device draws nothing until its Reset
    if (FAILED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &s_bb)) || !s_bb) {
        s_bb = NULL;
        return;
    }
    D3DSURFACE_DESC d;
    float s = SUCCEEDED(s_bb->GetDesc(&d)) ? ScaleFor(d.Width, d.Height) : 0.0f;
    if (s <= 0 || !Ready(dev, s)) {
        Restore(dev);
        return;
    }
    Refresh();
    Place(d.Width, d.Height, s);
    int n = BuildPanel();
    // Keep the game's state, then draw into the back buffer with nothing else bound.
    s_captured = SUCCEEDED(g_sb->Capture());
    if (!s_captured) {
        Restore(dev);
        return;
    }
    for (DWORD i = 0; i < g_maxRt; i++)
        if (FAILED(dev->GetRenderTarget(i, &s_rt[i]))) s_rt[i] = NULL;
    if (FAILED(dev->GetDepthStencilSurface(&s_ds))) s_ds = NULL;
    s_bound = TRUE;
    dev->SetRenderTarget(0, s_bb);
    for (DWORD i = 1; i < g_maxRt; i++)
        if (s_rt[i]) dev->SetRenderTarget(i, NULL);
    dev->SetDepthStencilSurface(NULL);
    SetDrawState(dev, d.Width, d.Height);
    if (g_testFault) *(volatile int*)(uintptr_t)0 = 0;   // [overlay] test_fault: an access violation mid-draw
    if (n >= 3 && SUCCEEDED(dev->BeginScene())) {
        s_inScene = TRUE;
        dev->DrawPrimitiveUP(D3DPT_TRIANGLELIST, (UINT)(n / 3), g_vtx, sizeof(Vtx));
        dev->EndScene();
        s_inScene = FALSE;
    }
    Restore(dev);
    if (s != g_loggedScale || d.Width != g_loggedW || d.Height != g_loggedH) {
        g_loggedScale = s;
        g_loggedW = d.Width;
        g_loggedH = d.Height;
        LogLine(L"overlay  panel shown on the %ux%u back buffer at scale %.2f (%s)", d.Width, d.Height, s, CORNERS[g_corner]);
    }
    NoteReadings();
}

static int OverlayFilter(EXCEPTION_POINTERS* ep, DWORD* code, void** at) {
    *code = ep->ExceptionRecord->ExceptionCode;
    *at = ep->ExceptionRecord->ExceptionAddress;
    return EXCEPTION_EXECUTE_HANDLER;
}

static void RestoreGuarded(IDirect3DDevice9* dev) {
    __try {
        Restore(dev);
        ReleaseObjects();
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

static void DrawGuarded(IDirect3DDevice9* dev) {
    DWORD code = 0;
    void* at = NULL;
    __try {
        DrawUnsafe(dev);
    } __except (OverlayFilter(GetExceptionInformation(), &code, &at)) {
        InterlockedExchange(&g_dead, 1);
        RestoreGuarded(dev);
        LogLine(L"overlay  the diagnostics panel stopped: exception 0x%08lx at 0x%p while drawing; it stays off for "
                L"this session and the game's drawing state was put back", code, at);
    }
}

// Edge-detected, and only while the game (its window's root owner) is in front.
static void PollKey() {
    HWND game = g_gameWindow, front = GetForegroundWindow();
    if (!game || !front || GetAncestor(front, GA_ROOTOWNER) != GetAncestor(game, GA_ROOTOWNER)) {
        g_keyHeld = TRUE;
        return;
    }
    BOOL down = (GetAsyncKeyState(g_vk) & 0x8000) != 0;
    if (down && !g_keyHeld) {
        g_visible = !g_visible;
        if (g_visible) {
            g_noteOpen = TRUE;
            g_openAt = 0;
        }
    }
    g_keyHeld = down;
}

void OverlayPresent(IDirect3DDevice9* dev) {
    if (!g_available || g_dead) return;
    PollKey();
    if (!g_visible) {
        if (g_tex || g_sb) {                                 // hidden holds no Direct3D object
            EnterCriticalSection(&g_lock);
            ReleaseObjects();
            LeaveCriticalSection(&g_lock);
        }
        return;
    }
    EnterCriticalSection(&g_lock);
    DrawGuarded(dev);
    LeaveCriticalSection(&g_lock);
}

void OverlayBeforeReset(IDirect3DDevice9*) {
    if (!g_lockReady) return;
    EnterCriticalSection(&g_lock);
    if (g_sb) g_sb->Release();                               // a state block must go before a Reset
    g_sb = NULL;
    LeaveCriticalSection(&g_lock);
}

void OverlayDeviceCreating() {
    if (!g_lockReady) return;
    EnterCriticalSection(&g_lock);
    ReleaseObjects();                                        // they belong to the previous device (and keep it alive)
    g_dev = NULL;
    LeaveCriticalSection(&g_lock);
}
