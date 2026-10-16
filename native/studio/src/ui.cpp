// Riftstone Studio's window: Direct2D and DirectWrite, drawn in the Forge look (src/riftstone/studio/index.html):
// obsidian surfaces, chrome hairlines, two cut corners, rift cyan only for what is live, selected or focused, ruby
// only as a critical marker. Every page is built into a display list (items) and a list of controls (ctls) from the
// state; painting and hit testing read the same lists, so the tests can click a control by its name.
#include "studio.h"
#include "version.h"

#include <commctrl.h>
#include <d2d1.h>
#include <d2d1helper.h>
#include <dwmapi.h>
#include <dwrite_3.h>
#include <shobjidl.h>
#include <wincodec.h>
#include <windowsx.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <cwchar>

using Microsoft::WRL::ComPtr;
using namespace studio;

namespace {

// -- the Forge values ----------------------------------------------------------------------------------------------
const uint32_t BASE = 0x08080c, SURFACE = 0x12131a, HOVER = 0x191c26, PRESSED = 0x202632, WELL = 0x0c0d13,
               TEXT = 0xe2e8f0, MUTED = 0x949daf, FAINT = 0x697386, LINE = 0x282d39, LINE2 = 0x1c2029,
               CYAN = 0x00f0ff, CYAN2 = 0x7ff8ff, RUBY = 0xdc2626, KNOB_OFF = 0x1a1d27;

D2D1_COLOR_F C(uint32_t rgb, float a = 1.f) { return D2D1::ColorF(rgb, a); }

const float RAIL = 236.f, PAD = 40.f, TOP = 116.f;
const UINT WM_APP_ENDEDIT = WM_APP + 1;
const UINT_PTR TIMER_POLL = 1, TIMER_LIVE = 2;

enum Page { HOME, FEATURES, SETTINGS, PROFILES, GRAPHICS, LIVE, PACK, PAGES };
const wchar_t* const kPageKey[PAGES] = {L"home", L"features", L"settings", L"profiles", L"graphics", L"live", L"pack"};
const wchar_t* const kPageTitle[PAGES] = {L"Home", L"Features", L"Settings", L"Profiles", L"Graphics", L"Live",
                                          L"Mod pack"};

// -- Direct2D and DirectWrite, once a process ------------------------------------------------------------------------
struct Gfx {
    ComPtr<ID2D1Factory> d2d;
    ComPtr<IDWriteFactory> dw;
    ComPtr<IDWriteInMemoryFontFileLoader> memLoader;
    ComPtr<IDWriteFontCollection1> blade;
    wstring bladeFamily;
    ComPtr<IDWriteTextFormat> brand, title, card, body, bodyBold, note, mono, button, nav, big, tag;
    ComPtr<ID2D1StrokeStyle> round;
    bool ok = false;

    // Riftstone Blade, the display face: a WOFF2 in the exe's resources, unpacked by DirectWrite (Windows 10 1703+).
    // Without it the headings fall back to Georgia, as Studio's page does.
    void LoadBlade() {
        ComPtr<IDWriteFactory5> dw5;
        if (FAILED(dw.As(&dw5))) return;
        HRSRC res = FindResourceW(nullptr, MAKEINTRESOURCEW(101), MAKEINTRESOURCEW(10) /* RT_RCDATA */);
        HGLOBAL mem = res ? LoadResource(nullptr, res) : nullptr;
        const void* data = mem ? LockResource(mem) : nullptr;
        DWORD size = res ? SizeofResource(nullptr, res) : 0;
        if (!data || !size) return;
        ComPtr<IDWriteFontFileStream> stream;
        if (FAILED(dw5->UnpackFontFile(DWRITE_CONTAINER_TYPE_WOFF2, data, size, &stream))) return;
        UINT64 len = 0;
        const void* frag = nullptr;
        void* ctx = nullptr;
        if (FAILED(stream->GetFileSize(&len)) || !len || len > (64u << 20) ||
            FAILED(stream->ReadFileFragment(&frag, 0, len, &ctx)))
            return;
        std::string bytes(static_cast<const char*>(frag), size_t(len));
        stream->ReleaseFileFragment(ctx);
        if (FAILED(dw5->CreateInMemoryFontFileLoader(&memLoader)) || FAILED(dw5->RegisterFontFileLoader(memLoader.Get())))
            return;
        ComPtr<IDWriteFontFile> file;
        ComPtr<IDWriteFontSetBuilder1> builder;
        ComPtr<IDWriteFontSet> set;
        if (FAILED(memLoader->CreateInMemoryFontFileReference(dw5.Get(), bytes.data(), UINT32(bytes.size()), nullptr,
                                                              &file)) ||
            FAILED(dw5->CreateFontSetBuilder(&builder)) || FAILED(builder->AddFontFile(file.Get())) ||
            FAILED(builder->CreateFontSet(&set)) || FAILED(dw5->CreateFontCollectionFromFontSet(set.Get(), &blade)))
            return;
        ComPtr<IDWriteFontFamily> fam;
        ComPtr<IDWriteLocalizedStrings> names;
        UINT32 n = 0;
        if (blade->GetFontFamilyCount() == 0 || FAILED(blade->GetFontFamily(0, &fam)) ||
            FAILED(fam->GetFamilyNames(&names)) || FAILED(names->GetStringLength(0, &n))) {
            blade.Reset();
            return;
        }
        bladeFamily.assign(n + 1, L'\0');
        names->GetString(0, &bladeFamily[0], n + 1);
        bladeFamily.resize(n);
    }

    bool Init() {
        if (ok) return true;
        if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, d2d.GetAddressOf())) ||
            FAILED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory),
                                       reinterpret_cast<IUnknown**>(dw.GetAddressOf()))))
            return false;
        LoadBlade();
        auto make = [&](ComPtr<IDWriteTextFormat>& f, bool display, const wchar_t* family, DWRITE_FONT_WEIGHT w,
                        float size) {
            IDWriteFontCollection* coll = display && blade ? blade.Get() : nullptr;
            const wchar_t* fam = display ? (blade ? bladeFamily.c_str() : L"Georgia") : family;
            return SUCCEEDED(dw->CreateTextFormat(fam, coll, w, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL,
                                                  size, L"en-us", &f));
        };
        const auto R = DWRITE_FONT_WEIGHT_NORMAL, S = DWRITE_FONT_WEIGHT_SEMI_BOLD;
        ok = make(brand, true, nullptr, R, 27.f) && make(title, true, nullptr, R, 30.f) &&
             make(card, false, L"Segoe UI", S, 15.f) && make(body, false, L"Segoe UI", R, 13.5f) &&
             make(bodyBold, false, L"Segoe UI", S, 13.5f) && make(note, false, L"Segoe UI", R, 12.f) &&
             make(mono, false, L"Consolas", R, 12.5f) && make(button, false, L"Segoe UI", S, 13.f) &&
             make(nav, false, L"Segoe UI", R, 14.f) && make(big, false, L"Segoe UI Semilight", R, 22.f) &&
             make(tag, false, L"Segoe UI", S, 10.5f) &&
             SUCCEEDED(d2d->CreateStrokeStyle(D2D1::StrokeStyleProperties(D2D1_CAP_STYLE_ROUND, D2D1_CAP_STYLE_ROUND,
                                                                          D2D1_CAP_STYLE_ROUND, D2D1_LINE_JOIN_ROUND),
                                              nullptr, 0, &round));
        return ok;
    }
} g;

float TextHeight(const wstring& text, IDWriteTextFormat* fmt, float width) {
    ComPtr<IDWriteTextLayout> lay;
    if (text.empty() || FAILED(g.dw->CreateTextLayout(text.c_str(), UINT32(text.size()), fmt, std::max(width, 1.f), 1e5f,
                                                      &lay)))
        return 0;
    lay->SetWordWrapping(DWRITE_WORD_WRAPPING_WRAP);
    DWRITE_TEXT_METRICS m{};
    lay->GetMetrics(&m);
    return std::ceil(m.height);
}

float TextWidth(const wstring& text, IDWriteTextFormat* fmt) {
    ComPtr<IDWriteTextLayout> lay;
    if (text.empty() || FAILED(g.dw->CreateTextLayout(text.c_str(), UINT32(text.size()), fmt, 1e5f, 1e5f, &lay)))
        return 0;
    lay->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);
    DWRITE_TEXT_METRICS m{};
    lay->GetMetrics(&m);
    return std::ceil(m.widthIncludingTrailingWhitespace);
}

// -- what a page shows ---------------------------------------------------------------------------------------------
enum ItemKind { IT_FILL, IT_CARD, IT_TEXT, IT_DOT, IT_HLINE, IT_VLINE, IT_MARK, IT_BRAND };

struct Item {
    ItemKind kind = IT_TEXT;
    D2D1_RECT_F r{};
    wstring text;
    IDWriteTextFormat* fmt = nullptr;
    uint32_t color = TEXT;
    float alpha = 1.f, tracking = 0.f;
    bool wrap = false, vcenter = false, sel = false, crit = false, content = false;
    int align = 0;   // 0 leading, 1 centre, 2 trailing
};

enum CtlKind { C_NAV, C_BUTTON, C_PRIMARY, C_TOGGLE, C_SLIDER, C_SEGMENT, C_FIELD, C_ITEM };

struct Ctl {
    wstring name;   // what the tests click: "nav:features", "toggle:enemy_cap", "slider:0", "export", ...
    CtlKind kind = C_BUTTON;
    D2D1_RECT_F r{};
    wstring label, value;
    bool enabled = true, on = false, content = true;
    double lo = 0, hi = 0, cur = 0;
    int digits = 0;   // a slider's decimals (0: whole numbers)
};

bool Inside(const D2D1_RECT_F& r, float x, float y) { return x >= r.left && x < r.right && y >= r.top && y < r.bottom; }

struct Res {   // what a render target owns
    ComPtr<ID2D1SolidColorBrush> solid;
    ComPtr<ID2D1LinearGradientBrush> chrome, chromeHi, silver, cyan, ruby;

    static ComPtr<ID2D1LinearGradientBrush> Grad(ID2D1RenderTarget* rt,
                                                 std::initializer_list<std::pair<float, D2D1_COLOR_F>> stops) {
        std::vector<D2D1_GRADIENT_STOP> s;
        for (const auto& st : stops) s.push_back({st.first, st.second});
        ComPtr<ID2D1GradientStopCollection> coll;
        ComPtr<ID2D1LinearGradientBrush> b;
        if (SUCCEEDED(rt->CreateGradientStopCollection(s.data(), UINT32(s.size()), &coll)))
            rt->CreateLinearGradientBrush(D2D1::LinearGradientBrushProperties({0, 0}, {1, 1}), coll.Get(), &b);
        return b;
    }

    static ComPtr<ID2D1LinearGradientBrush> Chrome(ID2D1RenderTarget* rt, float a) {
        return Grad(rt, {{0.f, C(0x303541, a)}, {.18f, C(0x788391, a)}, {.29f, C(0xe2e8f0, a)}, {.35f, C(0x596372, a)},
                         {.56f, C(0x242a35, a)}, {.78f, C(0xa7b2c1, a)}, {1.f, C(0x353d4a, a)}});
    }

    bool Make(ID2D1RenderTarget* rt) {
        rt->CreateSolidColorBrush(C(TEXT), &solid);
        chrome = Chrome(rt, .65f);
        chromeHi = Chrome(rt, .85f);
        silver = Grad(rt, {{0.f, C(0xffffff)}, {.36f, C(0xe4eaf1)}, {.58f, C(0x8b97a7)}, {.82f, C(0xdfe6ee)},
                           {1.f, C(0xaab5c3)}});
        cyan = Grad(rt, {{0.f, C(CYAN, .95f)}, {.55f, C(0x0096aa, .75f)}, {1.f, C(CYAN, .9f)}});
        ruby = Grad(rt, {{0.f, C(RUBY, .9f)}, {.22f, C(RUBY, .9f)}, {.38f, C(0x353d4a, .65f)}, {.56f, C(0x242a35, .65f)},
                         {.78f, C(0xa7b2c1, .65f)}, {1.f, C(0x353d4a, .65f)}});
        return solid && chrome && chromeHi && silver && cyan && ruby;
    }
};

// A CSS linear-gradient angle across a rectangle.
void Aim(ID2D1LinearGradientBrush* b, const D2D1_RECT_F& r, float deg) {
    float rad = deg * 3.14159265f / 180.f, dx = std::sin(rad), dy = -std::cos(rad);
    float w = r.right - r.left, h = r.bottom - r.top, len = std::fabs(w * dx) + std::fabs(h * dy);
    float cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    b->SetStartPoint({cx - dx * len / 2, cy - dy * len / 2});
    b->SetEndPoint({cx + dx * len / 2, cy + dy * len / 2});
}

ComPtr<ID2D1PathGeometry> CutPath(const D2D1_RECT_F& r, float c1, float c2) {
    ComPtr<ID2D1PathGeometry> p;
    ComPtr<ID2D1GeometrySink> s;
    if (FAILED(g.d2d->CreatePathGeometry(&p)) || FAILED(p->Open(&s))) return nullptr;
    s->BeginFigure({r.left + c1, r.top}, D2D1_FIGURE_BEGIN_FILLED);
    D2D1_POINT_2F pts[] = {{r.right, r.top}, {r.right, r.bottom - c2}, {r.right - c2, r.bottom}, {r.left, r.bottom},
                           {r.left, r.top + c1}};
    s->AddLines(pts, 5);
    s->EndFigure(D2D1_FIGURE_END_CLOSED);
    s->Close();
    return p;
}

D2D1_RECT_F Inset(const D2D1_RECT_F& r, float d) { return {r.left + d, r.top + d, r.right - d, r.bottom - d}; }

// A cut-corner shape: the outer fill (a chrome hairline) and the surface 1 DIP inside it.
void CutShape(ID2D1RenderTarget* rt, const D2D1_RECT_F& r, float c1, float c2, ID2D1Brush* edge, ID2D1Brush* fill) {
    auto outer = CutPath(r, c1, c2), inner = CutPath(Inset(r, 1.f), c1 - .4f, c2 - .4f);
    if (outer) rt->FillGeometry(outer.Get(), edge);
    if (inner) rt->FillGeometry(inner.Get(), fill);
}

// The mark (src/riftstone/studio/mark.svg), drawn from its own paths in its 64-unit box.
void DrawMark(ID2D1RenderTarget* rt, const D2D1_RECT_F& box) {
    float s = (box.right - box.left) / 64.f;
    D2D1_MATRIX_3X2_F old;
    rt->GetTransform(&old);
    rt->SetTransform(D2D1::Matrix3x2F::Scale(s, s) * D2D1::Matrix3x2F::Translation(box.left, box.top) * old);
    auto grad = [&](std::initializer_list<std::pair<float, D2D1_COLOR_F>> stops, D2D1_POINT_2F a, D2D1_POINT_2F b) {
        auto br = Res::Grad(rt, stops);
        if (br) {
            br->SetStartPoint(a);
            br->SetEndPoint(b);
        }
        return br;
    };
    auto path = [&](std::initializer_list<D2D1_POINT_2F> pts, bool curves) {
        ComPtr<ID2D1PathGeometry> p;
        ComPtr<ID2D1GeometrySink> sk;
        if (FAILED(g.d2d->CreatePathGeometry(&p)) || FAILED(p->Open(&sk))) return p;
        auto it = pts.begin();
        sk->BeginFigure(*it++, D2D1_FIGURE_BEGIN_FILLED);
        while (it != pts.end()) {
            if (curves) {
                D2D1_BEZIER_SEGMENT b{it[0], it[1], it[2]};
                sk->AddBezier(b);
                it += 3;
            } else sk->AddLine(*it++);
        }
        sk->EndFigure(D2D1_FIGURE_END_CLOSED);
        sk->Close();
        return p;
    };
    std::initializer_list<std::pair<float, D2D1_COLOR_F>> fang = {
        {0.f, C(0xf4f7fb)}, {.22f, C(0xa7b2c1)}, {.42f, C(0xeef2f7)}, {.62f, C(0x596372)}, {.84f, C(0xcdd6e0)},
        {1.f, C(0x6b7684)}};
    auto left = path({{18, 5}, {9, 13}, {5.5f, 27}, {8.5f, 39}, {11, 49}, {17, 56}, {25, 61.5f}, {19.5f, 53}, {15.2f, 44},
                      {14, 34}, {12.8f, 24}, {14.6f, 13}, {18, 5}},
                     true);
    auto right = path({{46, 5}, {55, 13}, {58.5f, 27}, {55.5f, 39}, {53, 49}, {47, 56}, {39, 61.5f}, {44.5f, 53},
                       {48.8f, 44}, {50, 34}, {51.2f, 24}, {49.4f, 13}, {46, 5}},
                      true);
    if (auto b = grad(fang, {7, 5}, {15.1f, 61.5f}); b && left) rt->FillGeometry(left.Get(), b.Get());
    if (auto b = grad(fang, {57, 5}, {48.9f, 61.5f}); b && right) rt->FillGeometry(right.Get(), b.Get());
    {
        std::vector<D2D1_GRADIENT_STOP> st = {{0.f, C(CYAN, .35f)}, {1.f, C(CYAN, 0.f)}};
        ComPtr<ID2D1GradientStopCollection> coll;
        ComPtr<ID2D1RadialGradientBrush> glow;
        if (SUCCEEDED(rt->CreateGradientStopCollection(st.data(), 2, &coll)) &&
            SUCCEEDED(rt->CreateRadialGradientBrush(D2D1::RadialGradientBrushProperties({32, 27.8f}, {0, 0}, 15, 22),
                                                    coll.Get(), &glow)))
            rt->FillEllipse(D2D1::Ellipse({32, 30}, 15, 22), glow.Get());
    }
    struct Facet {
        std::initializer_list<D2D1_POINT_2F> pts;
        uint32_t a, b;
        D2D1_POINT_2F from, to;
    };
    Facet facets[] = {{{{32, 3}, {20.5f, 25}, {32, 27}}, 0xeef3f9, 0x7d8898, {20.5f, 3}, {32, 27}},
                      {{{32, 3}, {32, 27}, {43.5f, 25}}, 0xb9c4d1, 0x404957, {43.5f, 3}, {32, 27}},
                      {{{20.5f, 25}, {32, 59}, {32, 27}}, 0x6c7888, 0x171b23, {20.5f, 25}, {32, 59}},
                      {{{32, 27}, {32, 59}, {43.5f, 25}}, 0x353d4a, 0x0b0d12, {43.5f, 25}, {32, 59}}};
    for (const auto& f : facets) {
        auto p = path(f.pts, false);
        if (auto b = grad({{0.f, C(f.a)}, {1.f, C(f.b)}}, f.from, f.to); b && p) rt->FillGeometry(p.Get(), b.Get());
    }
    ComPtr<ID2D1SolidColorBrush> sb;
    rt->CreateSolidColorBrush(C(TEXT, .45f), &sb);
    if (sb) {
        if (auto outline = path({{32, 3}, {43.5f, 25}, {32, 59}, {20.5f, 25}}, false))
            rt->DrawGeometry(outline.Get(), sb.Get(), .6f, g.round.Get());
        D2D1_POINT_2F crack[] = {{32.4f, 8}, {30.6f, 17}, {33.4f, 24.5f}, {30.4f, 33}, {33.2f, 41.5f}, {31, 50}, {32.2f, 55}};
        auto line = [&](uint32_t color, float a, float width) {
            sb->SetColor(C(color, a));
            for (size_t i = 0; i + 1 < std::size(crack); i++) rt->DrawLine(crack[i], crack[i + 1], sb.Get(), width, g.round.Get());
        };
        line(CYAN, .12f, 5.f);   // the SVG blurs a wide cyan stroke; two soft strokes stand in for the blur
        line(CYAN, .28f, 3.4f);
        line(CYAN2, 1.f, 1.2f);
    }
    rt->SetTransform(old);
}

// -- the window's state ----------------------------------------------------------------------------------------------
struct App {
    HINSTANCE inst = nullptr;
    HWND hwnd = nullptr;
    wstring game;
    std::vector<Feature> feats;
    SessionInfo session;
    wstring loaderVer;
    bool running = false;
    int page = HOME;
    wstring selStem, selIni, selRules, selTitle;
    std::vector<Setting> settings;
    float scroll[PAGES] = {};
    float maxScroll = 0;
    wstring status;
    bool statusBad = false;
    wstring exportName = L"My overhaul";
    bool havePlan = false;
    Profile plan;
    ApplyReport planReport;
    wstring planFile;
    std::vector<wstring> planSwitches;
    GraphicsInfo gfx;
    LiveStats live;
    bool haveLive = false;
    wstring liveName;   // a stand-in mapping (the smoke, screenshots); empty: the loader's own
    wstring packName = L"My Riftstone setup";
    bool havePack = false;
    PackReport pack;

    std::vector<Item> items;
    std::vector<Ctl> ctls;
    wstring hotName, pressedName, focusName;
    bool focusVisible = false, tracking = false;
    bool dragging = false;
    wstring dragName;
    double dragValue = 0;
    HWND edit = nullptr;
    wstring editName;
    HFONT editFont = nullptr;
    HBRUSH editBrush = nullptr;

    float dpi = 96.f, W = 1180.f, H = 760.f;   // DIPs
    ComPtr<ID2D1HwndRenderTarget> rt;
    Res res;

    bool smoke = false;
    wstring smokeSave, smokeOpen, smokePack;

    // ---------------------------------------------------------------------------------------------------------------
    float Px(float dip) const { return dip * dpi / 96.f; }
    float Dip(int px) const { return float(px) * 96.f / dpi; }

    void Say(const wstring& text, bool bad = false) {
        status = text;
        statusBad = bad;
    }

    void LoadSettings() {
        settings.clear();
        selIni.clear();
        selRules = selStem;
        selTitle = selStem;
        if (selStem.empty() || game.empty()) return;
        if (Lower(selStem) == kLoaderStem) {
            selIni = LoaderIni(game);
            selRules = kLoaderStem;
            selTitle = L"Loader";
        } else {
            for (const auto& f : feats)
                if (Lower(f.stem) == Lower(selStem)) {
                    selIni = f.ini();
                    selRules = f.stem;
                    selTitle = f.title;
                }
        }
        if (!selIni.empty()) ReadSettings(selIni, settings);
    }

    std::vector<std::pair<wstring, wstring>> SettingStems() const {   // stem, title
        std::vector<std::pair<wstring, wstring>> out;
        if (game.empty()) return out;
        for (const auto& f : feats)
            if (FileExists(f.ini())) out.push_back({f.stem, f.title});
        if (FileExists(LoaderIni(game))) out.push_back({kLoaderStem, L"Loader"});
        return out;
    }

    void Reload() {
        feats.clear();
        session = SessionInfo();
        loaderVer.clear();
        running = GameRunning();
        if (!game.empty()) {
            feats = ListFeatures(game);
            session = ReadSession(game);
            loaderVer = LoaderVersion(JoinPath(game, L"dinput8.dll"));
        }
        gfx = ReadGraphics(game);
        haveLive = ReadLive(0, live, liveName);
        auto stems = SettingStems();
        bool found = false;
        for (const auto& s : stems)
            if (Lower(s.first) == Lower(selStem)) found = true;
        if (!found) selStem = stems.empty() ? L"" : stems[0].first;
        LoadSettings();
    }

    void Changed() {
        Build();
        if (hwnd) InvalidateRect(hwnd, nullptr, FALSE);
    }

    // -- building a page ---------------------------------------------------------------------------------------------
    Item& Add(ItemKind k, D2D1_RECT_F r, bool content) {
        items.push_back(Item());
        Item& it = items.back();
        it.kind = k;
        it.r = r;
        it.content = content;
        return it;
    }

    Item& Text(float x, float y, float w, float h, const wstring& t, IDWriteTextFormat* f, uint32_t color, bool content,
               bool wrap = false) {
        Item& it = Add(IT_TEXT, {x, y, x + w, y + h}, content);
        it.text = t;
        it.fmt = f;
        it.color = color;
        it.wrap = wrap;
        return it;
    }

    Ctl& Control(CtlKind k, const wstring& name, D2D1_RECT_F r, const wstring& label, bool content = true) {
        ctls.push_back(Ctl());
        Ctl& c = ctls.back();
        c.kind = k;
        c.name = name;
        c.r = r;
        c.label = label;
        c.content = content;
        return c;
    }

    float ViewTop() const { return TOP; }
    float ViewBottom() const { return H - 52.f; }
    float Left() const { return RAIL + PAD; }
    float Right() const { return std::max(W - PAD, Left() + 360.f); }

    // A card with a title and a wrapped line under it; returns the y below the text.
    float CardText(float x, float y, float w, const wstring& title, const wstring& line, float textW = -1) {
        if (textW < 0) textW = w - 44;
        Text(x + 22, y + 18, textW, 22, title, g.card.Get(), TEXT, true);
        float h = TextHeight(line, g.body.Get(), textW);
        if (!line.empty()) Text(x + 22, y + 44, textW, h + 2, line, g.body.Get(), MUTED, true, true);
        return y + 44 + h;
    }

    Item& Card(float x, float y, float w, float h) { return Add(IT_CARD, {x, y, x + w, y + h}, true); }

    void Build() {
        items.clear();
        ctls.clear();
        BuildRail();
        float s = scroll[page];
        float bottom = TOP;
        switch (page) {
        case HOME: bottom = BuildHome(TOP - s); break;
        case FEATURES: bottom = BuildFeatures(TOP - s); break;
        case SETTINGS: bottom = BuildSettings(TOP - s); break;
        case PROFILES: bottom = BuildProfiles(TOP - s); break;
        case GRAPHICS: bottom = BuildGraphics(TOP - s); break;
        case LIVE: bottom = BuildLive(TOP - s); break;
        case PACK: bottom = BuildPack(TOP - s); break;
        }
        maxScroll = std::max(0.f, bottom + s + 24.f - ViewBottom());
        if (scroll[page] > maxScroll) {
            scroll[page] = maxScroll;
            Build();
            return;
        }
        // the header and the status line
        Text(Left(), 30, Right() - Left(), 44, kPageTitle[page], g.title.Get(), TEXT, false);
        static const wchar_t* const sub[PAGES] = {
            L"The game, the loader and the last session; start Dark Arisen from here.",
            L"Riftstone's features in the game's riftstone\\plugins folder. They switch while the game is closed.",
            L"Every setting of a feature, checked the way Riftstone checks it before it is written.",
            L"An overhaul in one small file: which features are on and their settings, to keep or share.",
            L"The game's own view settings, ENB, ReShade and DXVK, and how many enemies come at once.",
            L"The running game as its loader sees it, read every second: memory, frames, enemies, plugins.",
            L"This folder's Riftstone setup as one zip for another player. No game files, no mods."};
        Text(Left(), 76, Right() - Left(), 22, sub[page], g.body.Get(), MUTED, false);
        Add(IT_HLINE, {RAIL, TOP - 12, W, TOP - 11}, false).color = LINE2;
        Add(IT_HLINE, {RAIL, H - 46, W, H - 45}, false).color = LINE2;
        if (!status.empty()) {
            float x = Left();
            if (statusBad) {
                Item& d = Add(IT_FILL, {x, H - 30, x + 8, H - 22}, false);
                d.color = RUBY;
                x += 18;
            }
            Item& t = Text(x, H - 38, Right() - x, 24, status, g.body.Get(), TEXT, false);
            t.vcenter = true;
        }
    }

    void BuildRail() {
        Add(IT_FILL, {0, 0, RAIL, H}, false).color = WELL;
        Add(IT_VLINE, {RAIL - 1, 0, RAIL, H}, false).color = LINE2;
        Add(IT_MARK, {24, 26, 24 + 38, 26 + 38}, false);
        Item& b = Add(IT_BRAND, {72, 24, RAIL - 8, 58}, false);
        b.text = L"Riftstone";
        b.fmt = g.brand.Get();
        Item& t = Text(74, 58, RAIL - 80, 16, L"STUDIO", g.tag.Get(), MUTED, false);
        t.tracking = 3.f;
        float y = 112;
        for (int p = 0; p < PAGES; p++) {
            Ctl& c = Control(C_NAV, wstring(L"nav:") + kPageKey[p], {14, y, RAIL - 14, y + 40}, kPageTitle[p], false);
            c.on = page == p;
            y += 44;
        }
        // the game's state at the foot of the rail
        float fy = H - 96;
        uint32_t dot = game.empty() ? RUBY : running ? CYAN : loaderVer.empty() ? TEXT : FAINT;
        Item& d = Add(IT_DOT, {26, fy + 6, 33, fy + 13}, false);
        d.color = dot;
        wstring state = game.empty() ? L"No game folder" : running ? L"Dark Arisen is running" :
                        loaderVer.empty() ? L"Loader not installed" : L"Loader " + loaderVer;
        Text(42, fy, RAIL - 56, 20, state, g.note.Get(), game.empty() ? TEXT : MUTED, false);
        Text(26, H - 52, RAIL - 40, 18, L"Riftstone Studio " + Widen(kVersion), g.mono.Get(), FAINT, false);
    }

    float NoGame(float y) {
        float x = Left(), w = Right() - x;
        float end = CardText(x, y, w, L"Choose the game folder first",
                             L"Studio did not find Dragon's Dogma: Dark Arisen. On Home, choose the folder that holds "
                             L"DDDA.exe.");
        Card(x, y, w, end - y + 20);
        std::rotate(items.begin() + long(items.size()) - 1, items.end() - 1, items.end());
        return end + 20;
    }

    // Cards are added after their text so their height is known; this puts the card under the text again.
    void CardBehind(size_t firstText, float x, float y, float w, float h, bool sel = false, bool crit = false) {
        Item card;
        card.kind = IT_CARD;
        card.r = {x, y, x + w, y + h};
        card.content = true;
        card.sel = sel;
        card.crit = crit;
        items.insert(items.begin() + long(firstText), card);
    }

    float BuildHome(float y) {
        float x = Left(), w = Right() - x;
        size_t first = items.size();
        if (game.empty()) {
            float end = CardText(x, y, w, L"Dragon's Dogma: Dark Arisen was not found",
                                 L"Studio looks next to itself, then in every Steam library. Choose the folder that "
                                 L"holds DDDA.exe.",
                                 w - 260);
            float h = std::max(end - y + 22, 110.f);
            Control(C_PRIMARY, L"folder", {x + w - 214, y + 30, x + w - 24, y + 72}, L"Choose folder\x2026");
            CardBehind(first, x, y, w, h, false, true);
            return y + h;
        }
        Text(x + 22, y + 18, w - 300, 22, kGameTitle, g.card.Get(), TEXT, true);
        Text(x + 22, y + 46, w - 300, 18, game, g.mono.Get(), MUTED, true);
        wstring line = running ? L"Running now. Features and settings change at the game's next start."
                               : L"Riftstone's features start with the game. Nothing in the game's own files changes.";
        Text(x + 22, y + 72, w - 300, 40, line, g.body.Get(), MUTED, true, true);
        float h = 124;
        Ctl& launch = Control(C_PRIMARY, L"launch", {x + w - 240, y + 24, x + w - 24, y + 70},
                              running ? L"Running" : L"Launch Dark Arisen");
        launch.enabled = !running;
        Control(C_BUTTON, L"folder", {x + w - 240, y + 78, x + w - 24, y + 108}, L"Choose another folder\x2026");
        CardBehind(first, x, y, w, h, running);
        y += h + 16;

        float cw = (w - 32) / 3;
        // loader
        first = items.size();
        Text(x + 22, y + 18, cw - 44, 22, L"Loader", g.card.Get(), TEXT, true);
        Text(x + 22, y + 44, cw - 44, 30, loaderVer.empty() ? L"Not installed" : loaderVer, g.big.Get(), TEXT, true);
        Text(x + 22, y + 80, cw - 44, 60,
             loaderVer.empty() ? L"Unzip Riftstone's player zip into the game folder: the features need the loader "
                                 L"(dinput8.dll)."
                               : L"dinput8.dll in the game folder: it loads the features and writes the reports.",
             g.note.Get(), MUTED, true, true);
        CardBehind(first, x, y, cw, 150, false, loaderVer.empty());
        // features
        first = items.size();
        int on = 0;
        for (const auto& f : feats) on += f.on;
        float fx = x + cw + 16;
        Text(fx + 22, y + 18, cw - 44, 22, L"Features", g.card.Get(), TEXT, true);
        Text(fx + 22, y + 44, cw - 44, 30, std::to_wstring(on) + L" of " + std::to_wstring(feats.size()) + L" on",
             g.big.Get(), TEXT, true);
        Control(C_BUTTON, L"go:features", {fx + 22, y + 96, fx + 160, y + 128}, L"Features");
        CardBehind(first, fx, y, cw, 150);
        // last session
        first = items.size();
        float sx = x + 2 * (cw + 16);
        Text(sx + 22, y + 18, cw - 44, 22, L"Last session", g.card.Get(), TEXT, true);
        wstring when = session.started.empty() ? L"None recorded yet" : session.started;
        Text(sx + 22, y + 46, cw - 44, 20, when, g.mono.Get(), TEXT, true);
        wstring ended;
        if (!session.end.empty()) {
            ended = L"Ended: " + session.end;
            std::replace(ended.begin(), ended.end(), L'_', L' ');
        }
        if (session.uptimeMs >= 0) {
            long long t = session.uptimeMs / 1000;
            wchar_t buf[64];
            swprintf_s(buf, L"%s%lld:%02lld:%02lld played", ended.empty() ? L"" : L" \x00B7 ", t / 3600, t / 60 % 60, t % 60);
            ended += buf;
        }
        if (!session.newestReport.empty()) ended += L"\nNewest report: " + session.newestReport;
        Text(sx + 22, y + 74, cw - 44, 66, ended, g.note.Get(), MUTED, true, true);
        CardBehind(first, sx, y, cw, 150);
        y += 150 + 16;

        if (session.safeMode) {
            first = items.size();
            float end = CardText(x, y, w, L"Safe mode is on",
                                 L"The loader started the game without its features after crashes at start. Turn it "
                                 L"off once the cause is fixed, and the features run again at the next start.",
                                 w - 300);
            float h2 = std::max(end - y + 22, 100.f);
            Control(C_BUTTON, L"safemode", {x + w - 240, y + 30, x + w - 24, y + 66}, L"Turn safe mode off");
            CardBehind(first, x, y, w, h2, false, true);
            y += h2 + 16;
        }
        return y;
    }

    float BuildFeatures(float y) {
        if (game.empty()) return NoGame(y);
        float x = Left(), w = Right() - x;
        if (running) {
            size_t first = items.size();
            float end = CardText(x, y, w, L"The game is running",
                                 L"Features load when the game starts, so they switch once it is closed.");
            CardBehind(first, x, y, w, end - y + 20);
            y = end + 36;
        }
        if (feats.empty()) {
            size_t first = items.size();
            float end = CardText(x, y, w, L"No features installed",
                                 L"Unzip Riftstone's player zip into the game folder, then come back: its features "
                                 L"arrive in riftstone\\plugins\\off, ready to switch on.");
            CardBehind(first, x, y, w, end - y + 20);
            return end + 20;
        }
        for (const auto& f : feats) {
            size_t first = items.size();
            float tw = w - 150;
            Text(x + 22, y + 16, tw, 22, f.title, g.card.Get(), TEXT, true);
            wstring summary = f.known ? f.summary : L"Not one of Riftstone's features: it switches like the others.";
            float sh = TextHeight(summary, g.body.Get(), tw);
            Text(x + 22, y + 40, tw, sh + 2, summary, g.body.Get(), MUTED, true, true);
            Text(x + 22, y + 46 + sh, tw, 18, f.file + (f.on ? L"  in riftstone\\plugins" : L"  in riftstone\\plugins\\off"),
                 g.mono.Get(), FAINT, true);
            float h = std::max(72.f + sh, 84.f);
            Ctl& t = Control(C_TOGGLE, L"toggle:" + f.stem, {x + w - 74, y + h / 2 - 11, x + w - 34, y + h / 2 + 11},
                             f.title);
            t.on = f.on;
            t.enabled = !running;
            CardBehind(first, x, y, w, h, f.on);
            y += h + 12;
        }
        return y;
    }

    float BuildSettings(float y) {
        if (game.empty()) return NoGame(y);
        float x = Left(), w = Right() - x;
        auto stems = SettingStems();
        if (stems.empty()) {
            size_t first = items.size();
            float end = CardText(x, y, w, L"Nothing to set yet",
                                 L"The features' settings files arrive with them, and the loader's with the loader.");
            CardBehind(first, x, y, w, end - y + 20);
            return end + 20;
        }
        float ly = y;
        for (const auto& s : stems) {
            Ctl& c = Control(C_ITEM, L"stem:" + s.first, {x, ly, x + 210, ly + 38}, s.second);
            c.on = Lower(s.first) == Lower(selStem);
            ly += 42;
        }
        float px = x + 230, pw = Right() - px;
        Text(px, y + 2, pw, 24, selTitle, g.card.Get(), TEXT, true);
        Text(px, y + 28, pw, 18, selIni, g.mono.Get(), FAINT, true);
        float cy = y + 60;
        if (settings.empty()) {
            Text(px, cy, pw, 20, L"This settings file holds no settings.", g.body.Get(), MUTED, true);
            cy += 30;
        }
        const float cw = std::min(340.f, pw * .5f);
        wstring section = L"\x1F";
        for (size_t i = 0; i < settings.size(); i++) {
            const Setting& s = settings[i];
            if (s.section != section) {
                section = s.section;
                Item& t = Text(px, cy + 6, pw, 16, L"[" + section + L"]", g.tag.Get(), FAINT, true);
                t.tracking = 1.f;
                cy += 30;
            }
            float tw = pw - cw - 24;
            Text(px, cy, tw, 20, s.key, g.bodyBold.Get(), TEXT, true);
            float hh = TextHeight(s.help, g.note.Get(), tw);
            if (!s.help.empty()) Text(px, cy + 22, tw, hh + 2, s.help, g.note.Get(), MUTED, true, true);
            float rowH = std::max(22.f + hh, 34.f);
            BuildSettingControl(int(i), s, px + pw - cw, cy, cw);
            cy += rowH + 14;
            Add(IT_HLINE, {px, cy - 7, px + pw, cy - 6}, true).color = LINE2;
        }
        return std::max(ly, cy);
    }

    void BuildSettingControl(int i, const Setting& s, float x, float y, float w) {
        Check c = CheckFor(selRules, s.section, s.key);
        wstring idx = std::to_wstring(i);
        bool isNum = c.kind == RULE_INT || c.kind == RULE_FLOAT;
        if (c.kind == RULE_INT && c.lo == 0 && c.hi == 1) {
            Ctl& t = Control(C_TOGGLE, L"set:" + idx, {x + w - 40, y + 1, x + w, y + 23}, s.key);
            t.on = Trim(s.value) == L"1";
            return;
        }
        if (isNum && std::isfinite(c.lo) && std::isfinite(c.hi) && c.hi > c.lo) {
            Ctl& sl = Control(C_SLIDER, L"slider:" + idx, {x, y - 2, x + w - 96, y + 26}, s.key);
            sl.lo = c.lo;
            sl.hi = c.hi;
            double span = c.hi - c.lo;
            sl.digits = c.kind == RULE_INT ? 0 : span <= 2 ? 2 : span <= 20 ? 1 : 0;
            wchar_t* end = nullptr;
            double v = wcstod(s.value.c_str(), &end);
            sl.cur = (end && end != s.value.c_str() && std::isfinite(v)) ? std::clamp(v, c.lo, c.hi) : c.lo;
            if (dragging && dragName == sl.name) sl.cur = dragValue;
            wstring shown = dragging && dragName == sl.name ? SliderText(sl, dragValue) : s.value;
            Ctl& f = Control(C_FIELD, L"field:" + idx, {x + w - 84, y - 2, x + w, y + 26}, s.key);   // sl may move
            f.value = shown;
            return;
        }
        if (c.kind == RULE_CHOICE) {
            float total = 0;
            for (const auto& ch : c.choices) total += TextWidth(ch, g.button.Get()) + 26;
            if (total <= w) {
                float sx = x + w - total;
                for (const auto& ch : c.choices) {
                    float cwid = TextWidth(ch, g.button.Get()) + 26;
                    Ctl& seg = Control(C_SEGMENT, L"seg:" + idx + L":" + ch, {sx, y - 2, sx + cwid - 2, y + 26}, ch);
                    seg.on = Lower(Trim(s.value)) == ch;
                    seg.value = ch;
                    sx += cwid;
                }
                return;
            }
        }
        float fw = std::min(w, 220.f);
        Ctl& f = Control(C_FIELD, L"field:" + idx, {x + w - fw, y - 2, x + w, y + 26}, s.key);
        f.value = s.value;
    }

    static wstring SliderText(const Ctl& c, double v) {
        wchar_t buf[64];
        swprintf_s(buf, L"%.*f", c.digits, v);
        return buf;
    }

    double SliderAt(const Ctl& c, float x) const {
        float t = std::clamp((x - c.r.left - 8) / std::max(1.f, c.r.right - c.r.left - 16), 0.f, 1.f);
        double v = c.lo + t * (c.hi - c.lo), m = std::pow(10.0, c.digits);
        return std::clamp(std::round(v * m) / m, c.lo, c.hi);
    }

    float BuildProfiles(float y) {
        if (game.empty()) return NoGame(y);
        float x = Left(), w = Right() - x;
        size_t first = items.size();
        float end = CardText(x, y, w, L"Export a profile",
                             L"Every feature's switch and the settings Riftstone can check, in one small file. It holds "
                             L"no game data, no paths and no DLL names, so a profile from someone else cannot make the "
                             L"loader open a file.");
        float ry = end + 16;
        Text(x + 22, ry + 4, 60, 22, L"Name", g.bodyBold.Get(), TEXT, true);
        Ctl& name = Control(C_FIELD, L"exportname", {x + 84, ry, x + 84 + 300, ry + 30}, L"Name");
        name.value = exportName;
        Control(C_PRIMARY, L"export", {x + 400, ry - 2, x + 560, ry + 32}, L"Export\x2026");
        float h = ry + 52 - y;
        CardBehind(first, x, y, w, h);
        y += h + 16;

        first = items.size();
        end = CardText(x, y, w, L"Import a profile",
                       L"Studio checks the whole file and shows what it would change; nothing changes until you "
                       L"apply it. A feature that is not installed here, or a value its check refuses, is left out and "
                       L"named.",
                       w - 220);
        Control(C_BUTTON, L"import", {x + w - 180, y + 24, x + w - 24, y + 58}, L"Import\x2026");
        h = std::max(end - y + 22, 96.f);
        CardBehind(first, x, y, w, h);
        y += h + 16;

        if (havePlan) {
            first = items.size();
            Text(x + 22, y + 18, w - 44, 22, L"Ready to import: " + plan.name, g.card.Get(), TEXT, true);
            wstring from = FileName(planFile) + (plan.madeWith.empty() ? L"" : L" \x00B7 made with " + plan.madeWith);
            Text(x + 22, y + 44, w - 44, 18, from, g.mono.Get(), FAINT, true);
            float ly = y + 72;
            auto line = [&](const wstring& t, uint32_t color) {
                float th = TextHeight(t, g.body.Get(), w - 44);
                Text(x + 22, ly, w - 44, th + 2, t, g.body.Get(), color, true, true);
                ly += th + 4;
            };
            for (const auto& s : planSwitches) line(s, TEXT);
            for (const auto& wr : planReport.writes)
                line(L"set " + wr.file + L" [" + wr.section + L"] " + wr.key + L" = " + wr.value, TEXT);
            for (const auto& s : planReport.skipped) line(L"left out: " + s, MUTED);
            bool nothing = planReport.switched == 0 && planReport.set == 0;
            if (nothing) line(L"Nothing to change: this game folder already matches the profile.", MUTED);
            ly += 10;
            Ctl& apply = Control(C_PRIMARY, L"apply", {x + 22, ly, x + 182, ly + 34}, L"Apply");
            apply.enabled = !nothing && !running;
            Control(C_BUTTON, L"cancel", {x + 194, ly, x + 314, ly + 34}, L"Cancel");
            h = ly + 54 - y;
            CardBehind(first, x, y, w, h, true);
            y += h + 16;
        }
        return y;
    }

    // A row of a card: a bold name, its help under it; returns the row's height.
    float Row(float x, float y, float tw, const wstring& name, const wstring& help) {
        Text(x, y, tw, 20, name, g.bodyBold.Get(), TEXT, true);
        float hh = TextHeight(help, g.note.Get(), tw);
        if (!help.empty()) Text(x, y + 22, tw, hh + 2, help, g.note.Get(), MUTED, true, true);
        return std::max(22.f + hh, 34.f);
    }

    float BuildGraphics(float y) {
        float x = Left(), w = Right() - x;
        size_t first = items.size();
        if (!gfx.configFound) {
            wstring why = gfx.config.empty() ? L"LOCALAPPDATA is not set, so the game's config.ini cannot be found."
                          : !gfx.configProblem.empty()
                              ? gfx.configProblem
                              : L"No config.ini at " + gfx.config +
                                    L": start the game once and it writes its settings there.";
            float end = CardText(x, y, w, L"The game's view", why);
            CardBehind(first, x, y, w, end - y + 20);
            y = end + 36;
        } else {
            Text(x + 22, y + 18, w - 44, 22, L"The game's view", g.card.Get(), TEXT, true);
            Text(x + 22, y + 44, w - 44, 18, gfx.config, g.mono.Get(), FAINT, true);
            float ry = y + 80;
            const float cw = std::min(340.f, (w - 44) * .5f), tw = w - 44 - cw - 24, cx = x + w - 22 - cw;
            float rh = Row(x + 22, ry, tw, L"Field of view",
                           L"CameraFov, the game's own setting: 0 to 15, as its options write it. How many degrees a "
                           L"step adds is not measured.");
            {
                Ctl& sl = Control(C_SLIDER, L"fov", {cx, ry - 2, cx + cw - 96, ry + 26}, L"Field of view");
                sl.lo = 0;
                sl.hi = 15;
                sl.digits = 1;
                wchar_t* end = nullptr;
                double v = wcstod(gfx.fov.c_str(), &end);
                sl.cur = (end && end != gfx.fov.c_str() && std::isfinite(v)) ? std::clamp(v, 0.0, 15.0) : 0.0;
                if (dragging && dragName == L"fov") sl.cur = dragValue;
                sl.enabled = !running && !gfx.fov.empty();
                wstring shown = dragging && dragName == L"fov" ? SliderText(sl, dragValue)
                                : gfx.fov.empty()              ? L"none"
                                                               : gfx.fov;
                bool enabled = sl.enabled;
                Ctl& f = Control(C_FIELD, L"fovtext", {cx + cw - 84, ry - 2, cx + cw, ry + 26}, L"Field of view");
                f.value = shown;
                f.enabled = enabled;
            }
            ry += rh + 14;
            Add(IT_HLINE, {x + 22, ry - 7, x + w - 22, ry - 6}, true).color = LINE2;
            rh = Row(x + 22, ry, tw, L"View range",
                     L"ViewRange: how far the game draws and keeps enemies active. FAR doubles the distances, "
                     L"FARTHEST triples them and keeps every model at full detail.");
            wstring now;
            for (wchar_t ch : Trim(gfx.viewRange)) now += (ch >= L'a' && ch <= L'z') ? wchar_t(ch - 32) : ch;
            const wchar_t* const ranges[] = {L"NORMAL", L"FAR", L"FARTHEST"};
            float total = 0;
            for (const wchar_t* r : ranges) total += TextWidth(r, g.button.Get()) + 26;
            float sx = x + w - 22 - total;
            for (const wchar_t* r : ranges) {
                float sw = TextWidth(r, g.button.Get()) + 26;
                Ctl& seg = Control(C_SEGMENT, wstring(L"vr:") + r, {sx, ry - 2, sx + sw - 2, ry + 26}, r);
                seg.on = now == r;
                seg.value = r;
                seg.enabled = !running && !gfx.viewRange.empty();
                sx += sw;
            }
            ry += rh + 14;
            wstring note = running ? L"The game is running: it writes config.ini when it closes, over any change, "
                                     L"so these change once it is closed."
                                   : L"The game reads these when it starts.";
            float nh = TextHeight(note, g.note.Get(), w - 44);
            Text(x + 22, ry, w - 44, nh + 2, note, g.note.Get(), running ? TEXT : FAINT, true, true);
            float h = ry + nh + 20 - y;
            CardBehind(first, x, y, w, h);
            y += h + 16;
        }

        // ENB: its effects switch; its keys and proxy, as enblocal.ini says
        first = items.size();
        if (gfx.enb) {
            bool on = Lower(Trim(gfx.enbEffect)) == L"true";
            wstring line = gfx.enbEffect.empty()
                               ? L"enbseries.ini has no [GLOBAL] UseEffect, so Studio leaves it to ENB."
                               : wstring(L"enbseries.ini [GLOBAL] UseEffect: whether ENB draws its effects from the start.");
            if (!gfx.enbKeys.empty()) line += L" In game " + gfx.enbKeys + L" turns them on and off.";
            if (!gfx.enbProxy.empty()) line += L" ENB passes Direct3D on to " + gfx.enbProxy + L" (its [PROXY] setting).";
            float end = CardText(x, y, w, L"ENB", line, w - 150);
            float h = std::max(end - y + 22, 84.f);
            Ctl& t = Control(C_TOGGLE, L"enb", {x + w - 74, y + h / 2 - 11, x + w - 34, y + h / 2 + 11}, L"ENB's effects");
            t.on = on;
            t.enabled = !running && !gfx.enbEffect.empty();
            CardBehind(first, x, y, w, h, on);
            y += h + 16;
        } else {
            float end = CardText(x, y, w, L"ENB", L"Not in the game folder (no enbseries.ini beside DDDA.exe).");
            CardBehind(first, x, y, w, end - y + 20);
            y = end + 36;
        }

        // ReShade switches in the game; its keys are what there is to know
        first = items.size();
        wstring rs;
        if (gfx.reshade) {
            rs = gfx.reshadeEffects.empty() ? L"No key turns its effects on and off"
                                            : L"In game " + gfx.reshadeEffects + L" turns its effects on and off";
            if (!gfx.reshadeOverlay.empty()) rs += L"; " + gfx.reshadeOverlay + L" opens its overlay";
            rs += L". ReShade switches in the game, so there is nothing to set here.";
        } else rs = L"Not in the game folder (no ReShade.ini beside DDDA.exe).";
        float end = CardText(x, y, w, L"ReShade", rs);
        CardBehind(first, x, y, w, end - y + 20);
        y = end + 36;

        // DXVK through the loader's chain
        first = items.size();
        bool chained = Lower(gfx.chain) == Lower(D3d9ChainPath());
        bool can = true;
        wstring why;
        if (chained)
            why = L"On: DXVK draws the game, and Direct3D's copy of its textures stays out of the game's 4 GB of "
                  L"address space.";
        else if (!gfx.folderD3d9.empty()) {
            can = false;
            why = L"The game folder's own d3d9.dll (" + gfx.folderD3d9 + L") is in charge, so the loader chains no "
                  L"other.";
            if (!gfx.enbProxy.empty()) why += L" ENB passes Direct3D on to " + gfx.enbProxy + L".";
        } else if (!gfx.dxvkHere) {
            can = false;
            why = wstring(D3d9ChainPath()) + L" is not there: 'Riftstone.cmd loader d3d9 add <DXVK release>' puts "
                                             L"DXVK's 32-bit d3d9.dll there.";
        } else
            why = L"Off: Windows' own Direct3D 9. On, DXVK draws the game and keeps Direct3D's copy of its textures "
                  L"out of the game's address space.";
        if (!gfx.chain.empty() && !chained) why = L"[d3d9] chain = " + gfx.chain + L", set by hand. " + why;
        end = CardText(x, y, w, L"DXVK through the loader", why, w - 150);
        float h = std::max(end - y + 22, 84.f);
        {
            Ctl& t = Control(C_TOGGLE, L"dxvk", {x + w - 74, y + h / 2 - 11, x + w - 34, y + h / 2 + 11}, L"DXVK");
            t.on = chained;
            t.enabled = !running && !game.empty() && (chained || can);
        }
        CardBehind(first, x, y, w, h, chained);
        y += h + 16;

        // the crowd
        first = items.size();
        wstring crowd;
        if (game.empty()) crowd = L"Choose the game folder on Home first.";
        else if (!gfx.enemyCap)
            crowd = L"enemy_cap is not installed: unzip Riftstone's player zip into the game folder and its features "
                    L"arrive in riftstone\\plugins\\off.";
        else
            crowd = wstring(gfx.enemyCapOn ? L"enemy_cap is on" : L"enemy_cap is off") +
                    (gfx.slots >= 0 ? L", " + std::to_wstring(gfx.slots) + L" enemies at once" : L"") +
                    L". 64 at once turns it on with 64 slots, and past 40 collision_cap with it (it lifts the game's "
                    L"800 hit shapes a frame). The game takes it at its next start.";
        end = CardText(x, y, w, L"Enemies at once", crowd, w - 260);
        h = std::max(end - y + 22, 110.f);
        {
            Ctl& big = Control(C_PRIMARY, L"crowd:64", {x + w - 214, y + 24, x + w - 24, y + 62}, L"64 at once");
            big.enabled = gfx.enemyCap;
        }
        {
            Ctl& back = Control(C_BUTTON, L"crowd:30", {x + w - 214, y + 70, x + w - 24, y + 100}, L"Back to 30");
            back.enabled = gfx.enemyCap;
        }
        CardBehind(first, x, y, w, h, gfx.enemyCapOn && gfx.slots > 30);
        return y + h;
    }

    float BuildLive(float y) {
        float x = Left(), w = Right() - x;
        size_t first = items.size();
        if (!haveLive) {
            float end = CardText(x, y, w, L"No game is publishing live stats",
                                 L"Start Dark Arisen with the Riftstone loader (0.3 or later, [live] on). While it runs "
                                 L"this page reads what the loader publishes every second: memory, frames, enemies, "
                                 L"Direct3D and the plugins.");
            CardBehind(first, x, y, w, end - y + 20);
            return end + 20;
        }
        const uint64_t mb = 1 << 20;
        float cw = (w - 32) / 3;
        auto ms = [](uint32_t us) {
            wchar_t b[32];
            swprintf_s(b, L"%.1f ms", double(us) / 1000.0);
            return wstring(b);
        };
        auto stat = [&](float sx, const wstring& title, const wstring& big, const wstring& note, bool crit) {
            size_t f = items.size();
            Text(sx + 22, y + 18, cw - 44, 22, title, g.card.Get(), TEXT, true);
            Text(sx + 22, y + 44, cw - 44, 30, big, g.big.Get(), TEXT, true);
            Text(sx + 22, y + 80, cw - 44, 56, note, g.note.Get(), MUTED, true, true);
            CardBehind(f, sx, y, cw, 146, false, crit);
        };
        if (live.vaTotal)
            stat(x, L"Memory", Commas(live.vaUsed / mb) + L" MB",
                 L"of " + Commas(live.vaTotal / mb) + L" MB of address space; peak " + Commas(live.vaUsedPeak / mb) +
                     L" MB, largest free block " + Commas(live.vaLargestFree / mb) + L" MB",
                 live.Flag(LiveStats::ADDRESS_SPACE_LOW) || live.Flag(LiveStats::MEMORY_PRESSURE));
        else stat(x, L"Memory", L"\x2014", L"Not measured yet.", false);
        if (live.Flag(LiveStats::FRAME_TIMING) && live.frames)
            stat(x + cw + 16, L"Frames", live.Fps() + L" fps",
                 ms(live.frameUsAvg) + L" average, 99% under " + ms(live.frameUsP99) + L", worst " + ms(live.frameUsMax) +
                     L"; " + std::to_wstring(live.stutters) + L" stutters",
                 live.Flag(LiveStats::HANG));
        else
            stat(x + cw + 16, L"Frames", L"\x2014",
                 live.Flag(LiveStats::FRAME_TIMING) ? L"None drawn yet." : L"The loader's frame timing is off.",
                 live.Flag(LiveStats::HANG));
        if (live.enemiesActive >= 0) {
            wstring note = live.enemySlots >= 0 ? std::to_wstring(live.enemySlots) + L" slots" : L"slots not counted";
            if (live.stage >= 0) note += L" \x00B7 stage " + std::to_wstring(live.stage);
            stat(x + 2 * (cw + 16), L"Enemies",
                 std::to_wstring(live.enemiesActive) +
                     (live.enemiesUsable >= 0 ? L" of " + std::to_wstring(live.enemiesUsable) : L""),
                 note, false);
        }
        else
            stat(x + 2 * (cw + 16), L"Enemies", L"\x2014", L"Not counted (enemy_cap counts them).", false);
        y += 146 + 16;

        if (!live.frameTimesUs.empty()) {
            first = items.size();
            size_t n = live.frameTimesUs.size();
            Text(x + 22, y + 18, w - 44, 22, L"Frame times", g.card.Get(), TEXT, true);
            double top = std::max(16667.0, 2.0 * live.frameUsAvg);
            for (uint32_t t : live.frameTimesUs) top = std::max(top, double(t));
            Text(x + 22, y + 44, w - 44, 18,
                 L"The last " + std::to_wstring(n) + L" frames, newest on the right; the top is " +
                     ms(uint32_t(std::min(top, 4294967295.0))),
                 g.note.Get(), MUTED, true);
            float gx = x + 22, gy = y + 70, gw = w - 44, gh = 96;
            Add(IT_FILL, {gx, gy, gx + gw, gy + gh}, true).color = WELL;
            float bw = gw / 256.f, bx = gx + gw - float(n) * bw;
            for (uint32_t t : live.frameTimesUs) {
                float bh = std::max(1.f, float(double(t) / top) * gh);
                bool spike = live.frameUsAvg && t > 3ull * live.frameUsAvg;
                Item& b = Add(IT_FILL, {bx, gy + gh - bh, bx + std::max(1.f, bw * .8f), gy + gh}, true);
                b.color = spike ? TEXT : CYAN;
                b.alpha = spike ? 1.f : .55f;
                bx += bw;
            }
            if (live.frameUsAvg) {
                float ay = gy + gh - float(double(live.frameUsAvg) / top) * gh;
                Item& a = Add(IT_HLINE, {gx, ay, gx + gw, ay + 1}, true);
                a.color = TEXT;
                a.alpha = .35f;
            }
            float h = gh + 70 + 20;
            CardBehind(first, x, y, w, h);
            y += h + 16;
        }

        first = items.size();
        Text(x + 22, y + 18, w - 260, 22, L"What the loader says", g.card.Get(), TEXT, true);
        {
            Ctl& snap = Control(C_BUTTON, L"snapshot", {x + w - 214, y + 16, x + w - 24, y + 48}, L"Take a snapshot");
            snap.enabled = !live.Flag(LiveStats::EXITED);
        }
        float ly = y + 58;
        for (const auto& line : DescribeLive(live)) {
            float th = TextHeight(line, g.body.Get(), w - 44);
            Text(x + 22, ly, w - 44, th + 2, line, g.body.Get(), line.rfind(L"  ", 0) == 0 ? MUTED : TEXT, true, true);
            ly += th + 4;
        }
        float h = ly + 16 - y;
        CardBehind(first, x, y, w, h, false, live.Flag(LiveStats::HANG) || live.Flag(LiveStats::ADDRESS_SPACE_LOW));
        return y + h;
    }

    float BuildPack(float y) {
        if (game.empty()) return NoGame(y);
        float x = Left(), w = Right() - x;
        size_t first = items.size();
        float end = CardText(x, y, w, L"Export a mod pack",
                             L"The loader, Riftstone's own features switched on and off as they are here, their settings "
                             L"and a profile, in one zip with a README for the player who unzips it into their game "
                             L"folder. It holds no game files and no mods; other authors' DLLs, DXVK and ENB stay "
                             L"behind, and the README names them.");
        float ry = end + 16;
        Text(x + 22, ry + 4, 60, 22, L"Name", g.bodyBold.Get(), TEXT, true);
        {
            Ctl& name = Control(C_FIELD, L"packname", {x + 84, ry, x + 84 + 300, ry + 30}, L"Name");
            name.value = packName;
        }
        {
            Ctl& b = Control(C_PRIMARY, L"pack", {x + 400, ry - 2, x + 560, ry + 32}, L"Export\x2026");
            b.enabled = !loaderVer.empty();
        }
        ry += 52;
        if (loaderVer.empty()) {
            wstring note = L"This game folder has no Riftstone loader (dinput8.dll) for the pack to carry: unzip "
                           L"Riftstone's player zip into it first.";
            float nh = TextHeight(note, g.note.Get(), w - 44);
            Text(x + 22, ry - 8, w - 44, nh + 2, note, g.note.Get(), TEXT, true, true);
            ry += nh + 8;
        }
        float h = ry - y;
        CardBehind(first, x, y, w, h, false, loaderVer.empty());
        y += h + 16;
        if (havePack) {
            first = items.size();
            Text(x + 22, y + 18, w - 44, 22, L"Wrote " + FileName(pack.out), g.card.Get(), TEXT, true);
            Text(x + 22, y + 44, w - 44, 18, pack.out, g.mono.Get(), FAINT, true);
            float ly = y + 74;
            auto line = [&](const wstring& t, IDWriteTextFormat* f, uint32_t color) {
                float th = TextHeight(t, f, w - 44);
                Text(x + 22, ly, w - 44, th + 2, t, f, color, true, true);
                ly += th + 4;
            };
            line(std::to_wstring(pack.files.size()) + L" files, " + Commas(pack.bytes) + L" bytes", g.bodyBold.Get(), TEXT);
            for (const auto& f : pack.files) line(f, g.mono.Get(), MUTED);
            ly += 8;
            line(L"Left behind", g.bodyBold.Get(), TEXT);
            for (const auto& s : pack.leftOut) line(s, g.body.Get(), MUTED);
            h = ly + 16 - y;
            CardBehind(first, x, y, w, h, true);
            y += h + 16;
        }
        return y;
    }

    // -- painting ----------------------------------------------------------------------------------------------------
    void DrawText(ID2D1RenderTarget* t, const Item& it, ID2D1Brush* brush) {
        ComPtr<IDWriteTextLayout> lay;
        float w = std::max(1.f, it.r.right - it.r.left), h = std::max(1.f, it.r.bottom - it.r.top);
        if (it.text.empty() || !it.fmt ||
            FAILED(g.dw->CreateTextLayout(it.text.c_str(), UINT32(it.text.size()), it.fmt, w, h, &lay)))
            return;
        lay->SetWordWrapping(it.wrap ? DWRITE_WORD_WRAPPING_WRAP : DWRITE_WORD_WRAPPING_NO_WRAP);
        lay->SetTextAlignment(it.align == 1 ? DWRITE_TEXT_ALIGNMENT_CENTER
                              : it.align == 2 ? DWRITE_TEXT_ALIGNMENT_TRAILING : DWRITE_TEXT_ALIGNMENT_LEADING);
        lay->SetParagraphAlignment(it.vcenter ? DWRITE_PARAGRAPH_ALIGNMENT_CENTER : DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
        if (!it.wrap) {
            ComPtr<IDWriteInlineObject> sign;
            if (SUCCEEDED(g.dw->CreateEllipsisTrimmingSign(lay.Get(), &sign))) {
                DWRITE_TRIMMING trim{DWRITE_TRIMMING_GRANULARITY_CHARACTER, 0, 0};
                lay->SetTrimming(&trim, sign.Get());
            }
        }
        if (it.tracking > 0) {
            ComPtr<IDWriteTextLayout1> l1;
            if (SUCCEEDED(lay.As(&l1))) l1->SetCharacterSpacing(0, it.tracking, 0, {0, UINT32(it.text.size())});
        }
        t->DrawTextLayout({it.r.left, it.r.top}, lay.Get(), brush);
    }

    void DrawCard(ID2D1RenderTarget* t, const Item& it) {
        ID2D1LinearGradientBrush* edge = it.crit ? res.ruby.Get() : res.chrome.Get();
        Aim(edge, it.r, 115);
        res.solid->SetColor(C(SURFACE));
        CutShape(t, it.r, 12, 20, edge, res.solid.Get());
        res.solid->SetColor(C(CYAN, .16f));
        t->FillRectangle({it.r.left + 28, it.r.top, it.r.left + 104, it.r.top + 1}, res.solid.Get());
        if (it.sel) {
            res.solid->SetColor(C(CYAN, .7f));
            t->FillRectangle({it.r.left + 1, it.r.top + 12, it.r.left + 3, it.r.bottom - 1}, res.solid.Get());
        }
    }

    void DrawItem(ID2D1RenderTarget* t, const Item& it) {
        switch (it.kind) {
        case IT_FILL:
        case IT_HLINE:
        case IT_VLINE:
            res.solid->SetColor(C(it.color, it.alpha));
            t->FillRectangle(it.r, res.solid.Get());
            break;
        case IT_DOT:
            res.solid->SetColor(C(it.color, it.alpha));
            t->FillEllipse(D2D1::Ellipse({(it.r.left + it.r.right) / 2, (it.r.top + it.r.bottom) / 2},
                                         (it.r.right - it.r.left) / 2, (it.r.bottom - it.r.top) / 2),
                           res.solid.Get());
            break;
        case IT_CARD: DrawCard(t, it); break;
        case IT_MARK: DrawMark(t, it.r); break;
        case IT_BRAND:
            res.silver->SetStartPoint({0, it.r.top + 4});
            res.silver->SetEndPoint({0, it.r.bottom - 2});
            DrawText(t, it, res.silver.Get());
            break;
        case IT_TEXT:
            res.solid->SetColor(C(it.color, it.alpha));
            DrawText(t, it, res.solid.Get());
            break;
        }
    }

    void Label(ID2D1RenderTarget* t, const D2D1_RECT_F& r, const wstring& text, IDWriteTextFormat* f, uint32_t color,
               int align = 1) {
        Item it;
        it.r = r;
        it.text = text;
        it.fmt = f;
        it.align = align;
        it.vcenter = true;
        res.solid->SetColor(C(color));
        DrawText(t, it, res.solid.Get());
    }

    void DrawCtl(ID2D1RenderTarget* t, const Ctl& c) {
        bool hot = c.enabled && c.name == hotName, down = hot && c.name == pressedName;
        bool focus = focusVisible && c.name == focusName;
        if (!c.enabled) t->PushLayer(D2D1::LayerParameters(D2D1::InfiniteRect(), nullptr, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
                                                           D2D1::IdentityMatrix(), .4f),
                                     nullptr);
        switch (c.kind) {
        case C_NAV:
        case C_ITEM: {
            if (c.on || hot) {
                res.solid->SetColor(C(c.on ? HOVER : SURFACE));
                t->FillRectangle(c.r, res.solid.Get());
            }
            if (c.on) {
                res.solid->SetColor(C(CYAN));
                t->FillRectangle({c.r.left, c.r.top + 8, c.r.left + 2, c.r.bottom - 8}, res.solid.Get());
            }
            Label(t, {c.r.left + 16, c.r.top, c.r.right - 8, c.r.bottom}, c.label,
                  c.kind == C_NAV ? g.nav.Get() : g.body.Get(), c.on || hot ? TEXT : MUTED, 0);
            break;
        }
        case C_BUTTON:
        case C_PRIMARY: {
            ID2D1LinearGradientBrush* edge = c.kind == C_PRIMARY ? res.cyan.Get() : hot ? res.chromeHi.Get() : res.chrome.Get();
            Aim(edge, c.r, 115);
            res.solid->SetColor(C(down ? PRESSED : hot ? HOVER : SURFACE));
            if (focus) {
                ComPtr<ID2D1SolidColorBrush> cy;
                t->CreateSolidColorBrush(C(CYAN), &cy);
                CutShape(t, c.r, 7, 10, cy.Get(), res.solid.Get());
            } else CutShape(t, c.r, 7, 10, edge, res.solid.Get());
            if (c.kind == C_PRIMARY) {
                auto inner = CutPath(Inset(c.r, 1.f), 6.6f, 9.6f);
                res.solid->SetColor(C(CYAN, hot ? .14f : .08f));
                if (inner) t->FillGeometry(inner.Get(), res.solid.Get());
            }
            Label(t, c.r, c.label, g.button.Get(), TEXT);
            break;
        }
        case C_TOGGLE: {
            D2D1_ROUNDED_RECT rr{Inset(c.r, .5f), 11, 11};
            res.solid->SetColor(c.on ? C(CYAN, .16f) : C(KNOB_OFF));
            t->FillRoundedRectangle(rr, res.solid.Get());
            res.solid->SetColor(c.on ? C(CYAN, .7f) : C(hot ? FAINT : LINE));
            t->DrawRoundedRectangle(rr, res.solid.Get(), 1.f);
            float kx = c.on ? c.r.right - 11 : c.r.left + 11;
            res.solid->SetColor(C(c.on ? CYAN : FAINT));
            t->FillEllipse(D2D1::Ellipse({kx, (c.r.top + c.r.bottom) / 2}, 7, 7), res.solid.Get());
            if (focus) {
                res.solid->SetColor(C(CYAN, .85f));
                t->DrawRoundedRectangle({Inset(c.r, -3.f), 14, 14}, res.solid.Get(), 2.f);
            }
            break;
        }
        case C_SLIDER: {
            float cy = (c.r.top + c.r.bottom) / 2, l = c.r.left + 8, r = c.r.right - 8;
            float tpos = float((c.cur - c.lo) / (c.hi - c.lo));
            float kx = l + tpos * (r - l);
            res.solid->SetColor(C(WELL));
            t->FillRectangle({l, cy - 2, r, cy + 2}, res.solid.Get());
            res.solid->SetColor(C(LINE));
            t->DrawRectangle({l, cy - 2, r, cy + 2}, res.solid.Get(), 1.f);
            res.solid->SetColor(C(TEXT, .55f));
            t->FillRectangle({l, cy - 1.5f, kx, cy + 1.5f}, res.solid.Get());
            bool held = focus || (dragging && dragName == c.name);
            res.solid->SetColor(C(held ? CYAN : TEXT));
            t->FillEllipse(D2D1::Ellipse({kx, cy}, 7, 7), res.solid.Get());
            res.solid->SetColor(C(BASE));
            t->DrawEllipse(D2D1::Ellipse({kx, cy}, 7, 7), res.solid.Get(), 1.f);
            Label(t, {l, c.r.bottom - 2, l + 60, c.r.bottom + 12}, SliderText(c, c.lo), g.note.Get(), FAINT, 0);
            Label(t, {r - 60, c.r.bottom - 2, r, c.r.bottom + 12}, SliderText(c, c.hi), g.note.Get(), FAINT, 2);
            break;
        }
        case C_SEGMENT: {
            res.solid->SetColor(C(hot ? HOVER : WELL));
            t->FillRectangle(c.r, res.solid.Get());
            res.solid->SetColor(C(c.on ? LINE : LINE2));
            t->DrawRectangle(Inset(c.r, .5f), res.solid.Get(), 1.f);
            if (c.on) {
                res.solid->SetColor(C(CYAN));
                t->FillRectangle({c.r.left + 6, c.r.bottom - 2, c.r.right - 6, c.r.bottom}, res.solid.Get());
            }
            if (focus) {
                res.solid->SetColor(C(CYAN, .85f));
                t->DrawRectangle(Inset(c.r, -2.f), res.solid.Get(), 1.5f);
            }
            Label(t, c.r, c.label, g.button.Get(), c.on || hot ? TEXT : MUTED);
            break;
        }
        case C_FIELD: {
            res.solid->SetColor(C(WELL));
            t->FillRectangle(c.r, res.solid.Get());
            bool editing = edit && editName == c.name;
            res.solid->SetColor(C(editing || focus ? CYAN : hot ? FAINT : LINE, editing || focus ? .85f : 1.f));
            t->DrawRectangle(Inset(c.r, .5f), res.solid.Get(), 1.f);
            if (!editing) Label(t, {c.r.left + 9, c.r.top, c.r.right - 8, c.r.bottom}, c.value, g.mono.Get(), TEXT, 0);
            break;
        }
        }
        if (!c.enabled) t->PopLayer();
    }

    void Paint(ID2D1RenderTarget* t) {
        t->Clear(C(BASE));
        D2D1_RECT_F view{RAIL, ViewTop() - 8, W, ViewBottom()};
        for (const auto& it : items)
            if (!it.content) DrawItem(t, it);
        t->PushAxisAlignedClip(view, D2D1_ANTIALIAS_MODE_ALIASED);
        for (const auto& it : items)
            if (it.content) DrawItem(t, it);
        for (const auto& c : ctls)
            if (c.content) DrawCtl(t, c);
        t->PopAxisAlignedClip();
        for (const auto& c : ctls)
            if (!c.content) DrawCtl(t, c);
        if (maxScroll > 0) {   // where the page is
            float vh = ViewBottom() - ViewTop(), bar = std::max(30.f, vh * vh / (vh + maxScroll));
            float top = ViewTop() + (vh - bar) * scroll[page] / maxScroll;
            res.solid->SetColor(C(LINE));
            t->FillRectangle({W - 7, top, W - 4, top + bar}, res.solid.Get());
        }
    }

    bool EnsureTarget() {
        if (rt) return true;
        RECT rc;
        GetClientRect(hwnd, &rc);
        auto props = D2D1::RenderTargetProperties(D2D1_RENDER_TARGET_TYPE_DEFAULT,
                                                  D2D1::PixelFormat(DXGI_FORMAT_UNKNOWN, D2D1_ALPHA_MODE_UNKNOWN), dpi, dpi);
        if (FAILED(g.d2d->CreateHwndRenderTarget(
                props, D2D1::HwndRenderTargetProperties(hwnd, D2D1::SizeU(UINT(std::max<LONG>(rc.right, 1)), UINT(std::max<LONG>(rc.bottom, 1)))),
                &rt)))
            return false;
        if (!res.Make(rt.Get())) {
            rt.Reset();
            return false;
        }
        return true;
    }

    void OnPaint() {
        PAINTSTRUCT ps;
        BeginPaint(hwnd, &ps);
        if (EnsureTarget()) {
            rt->BeginDraw();
            Paint(rt.Get());
            if (rt->EndDraw() == D2DERR_RECREATE_TARGET) {
                rt.Reset();
                res = Res();
                InvalidateRect(hwnd, nullptr, FALSE);
            }
        }
        EndPaint(hwnd, &ps);
    }

    // -- input -------------------------------------------------------------------------------------------------------
    D2D1_RECT_F View() const { return {RAIL, ViewTop() - 8, W, ViewBottom()}; }

    const Ctl* Hit(float x, float y) const {
        for (auto it = ctls.rbegin(); it != ctls.rend(); ++it) {
            if (it->content && !Inside(View(), x, y)) continue;
            if (Inside(it->r, x, y)) return &*it;
        }
        return nullptr;
    }

    const Ctl* Find(const wstring& name) const {
        for (const auto& c : ctls)
            if (c.name == name) return &c;
        return nullptr;
    }

    void EnsureVisible(const wstring& name) {
        const Ctl* c = Find(name);
        if (!c || !c->content) return;
        float s = scroll[page];
        if (c->r.top < ViewTop()) s -= ViewTop() - c->r.top + 12;
        else if (c->r.bottom > ViewBottom()) s += c->r.bottom - ViewBottom() + 12;
        else return;
        scroll[page] = std::clamp(s, 0.f, maxScroll);
        Build();
    }

    void Activate(const Ctl& c) {
        if (!c.enabled) return;
        const wstring& n = c.name;
        auto after = [&](const wchar_t* prefix) { return n.substr(wcslen(prefix)); };
        if (n.rfind(L"nav:", 0) == 0) {
            for (int p = 0; p < PAGES; p++)
                if (after(L"nav:") == kPageKey[p]) page = p;
            if (page == GRAPHICS) gfx = ReadGraphics(game);
            if (page == LIVE) haveLive = ReadLive(0, live, liveName);
            Say(L"");
        } else if (n == L"go:features") {
            page = FEATURES;
        } else if (n == L"launch") {
            DWORD pid = 0;
            wstring error;
            if (Launch(game, pid, error)) {
                running = true;
                Say(L"Dark Arisen is starting (process " + std::to_wstring(pid) + L").");
            } else Say(error, true);
        } else if (n == L"folder") {
            ChooseFolder();
        } else if (n == L"safemode") {
            wstring error;
            if (EndSafeMode(game, error)) {
                Reload();
                Say(L"Safe mode is off: the loader runs its features at the next start.");
            } else Say(error, true);
        } else if (n.rfind(L"toggle:", 0) == 0) {
            bool changed = false;
            wstring error, stem = after(L"toggle:");
            if (Toggle(game, stem, !c.on, changed, error)) {
                Reload();
                Say(c.label + (c.on ? L" is off." : L" is on.") + L" It takes effect at the game's next start.");
            } else Say(error, true);
        } else if (n.rfind(L"stem:", 0) == 0) {
            selStem = after(L"stem:");
            LoadSettings();
            scroll[SETTINGS] = 0;
            Say(L"");
        } else if (n.rfind(L"set:", 0) == 0) {
            Commit(_wtoi(after(L"set:").c_str()), c.on ? L"0" : L"1");
        } else if (n.rfind(L"seg:", 0) == 0) {
            wstring rest = after(L"seg:");
            Commit(_wtoi(rest.c_str()), c.value);
        } else if (n.rfind(L"field:", 0) == 0 || n == L"exportname" || n == L"fovtext" || n == L"packname") {
            BeginEdit(c);
            return;
        } else if (n.rfind(L"vr:", 0) == 0) {
            SetConfig(L"ViewRange", c.value);
        } else if (n == L"enb" || n == L"dxvk") {
            wstring error;
            bool ok = n == L"enb" ? SetEnbEffect(game, !c.on, error) : SetDxvkChain(game, !c.on, error);
            gfx = ReadGraphics(game);
            if (!ok) Say(error, true);
            else if (n == L"enb") Say(c.on ? L"ENB's effects are off from the start (enbseries.ini UseEffect = false)."
                                           : L"ENB's effects are on from the start (enbseries.ini UseEffect = true).");
            else Say(c.on ? L"[d3d9] chain cleared: Windows' own Direct3D 9 at the game's next start."
                          : L"[d3d9] chain = " + wstring(D3d9ChainPath()) + L": DXVK draws the game at its next start.");
        } else if (n.rfind(L"crowd:", 0) == 0) {
            wstring done, error;
            if (SetEnemyCrowd(game, _wtoi(after(L"crowd:").c_str()), done, error)) {
                Reload();
                Say(done);
            } else Say(error, true);
        } else if (n == L"snapshot") {
            wstring error;
            if (RequestSnapshot(live.pid, error))
                Say(L"Asked the game for a snapshot: the loader writes snapshot-*.txt and its .dmp into riftstone\\logs "
                    L"in a few seconds.");
            else Say(error, true);
        } else if (n == L"pack") {
            ExportPackNow();
        } else if (n == L"export") {
            Export();
        } else if (n == L"import") {
            Import();
        } else if (n == L"apply") {
            ApplyPlan();
        } else if (n == L"cancel") {
            havePlan = false;
            Say(L"");
        }
        Changed();
    }

    void SetConfig(const wstring& key, const wstring& value) {
        wstring written, error;
        if (!SetGameConfig(key, value, written, error)) {
            Say(error, true);
            return;
        }
        gfx = ReadGraphics(game);
        Say(L"config.ini [GRAPHICS] " + key + L" = " + written + L": the game reads it at its next start.");
    }

    void SliderCommit(const Ctl& c, double v) {
        if (c.name == L"fov") SetConfig(L"CameraFov", SliderText(c, v));
        else Commit(_wtoi(c.name.c_str() + 7), SliderText(c, v));
    }

    void Commit(int i, const wstring& value) {
        if (i < 0 || size_t(i) >= settings.size()) return;
        Setting s = settings[size_t(i)];
        wstring clean, error;
        if (!Validate(selRules, s.section, s.key, value, clean, error)) {
            Say(error, true);
            return;
        }
        if (clean == s.value) {
            Say(s.key + L" is already " + clean + L".");
            return;
        }
        if (!WriteSetting(selIni, s.section, s.key, clean, error)) {
            Say(error, true);
            return;
        }
        LoadSettings();
        Say(FileName(selIni) + L" [" + s.section + L"] " + s.key + L" = " + clean +
            (running ? L"  (the game reads it at its next start)" : L""));
    }

    void BeginEdit(const Ctl& c) {
        EndEdit(false);
        if (!hwnd) return;
        int fontPx = int(std::lround(Px(13.f)));
        if (!editFont) {
            editFont = CreateFontW(-fontPx, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                   CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
        }
        if (!editBrush) editBrush = CreateSolidBrush(RGB(0x0c, 0x0d, 0x13));
        int lineH = int(std::lround(Px(13.f) * 1.25f)) + 2;
        int left = int(Px(c.r.left + 2)), right = int(Px(c.r.right - 2));
        int mid = int(Px((c.r.top + c.r.bottom) / 2));
        edit = CreateWindowExW(0, L"EDIT", c.value.c_str(), WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, left, mid - lineH / 2,
                               right - left, lineH, hwnd, HMENU(UINT_PTR(100)), inst, nullptr);
        if (!edit) return;
        editName = c.name;
        SendMessageW(edit, WM_SETFONT, WPARAM(editFont), TRUE);
        SendMessageW(edit, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(int(Px(6)), int(Px(6))));
        SendMessageW(edit, EM_LIMITTEXT, 256, 0);
        SetWindowSubclass(edit, EditProc, 1, DWORD_PTR(this));
        if (!smoke) SetFocus(edit);
        SendMessageW(edit, EM_SETSEL, 0, -1);
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    static LRESULT CALLBACK EditProc(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR ref) {
        App* a = reinterpret_cast<App*>(ref);
        switch (m) {
        case WM_GETDLGCODE: return DLGC_WANTALLKEYS;
        case WM_KEYDOWN:
            if (w == VK_RETURN || w == VK_ESCAPE || w == VK_TAB) {
                PostMessageW(a->hwnd, WM_APP_ENDEDIT, w != VK_ESCAPE, 0);
                return 0;
            }
            break;
        case WM_CHAR:
            if (w == L'\r' || w == 27 || w == L'\t') return 0;
            break;
        case WM_KILLFOCUS: PostMessageW(a->hwnd, WM_APP_ENDEDIT, 1, 0); break;
        case WM_NCDESTROY: RemoveWindowSubclass(h, EditProc, 1); break;
        }
        return DefSubclassProc(h, m, w, l);
    }

    void EndEdit(bool commit) {
        if (!edit) return;
        int n = GetWindowTextLengthW(edit);
        wstring text(size_t(n) + 1, L'\0');
        GetWindowTextW(edit, &text[0], n + 1);
        text.resize(size_t(n));
        HWND e = edit;
        wstring name = editName;
        edit = nullptr;
        editName.clear();
        DestroyWindow(e);
        if (hwnd && !smoke) SetFocus(hwnd);
        if (commit) {
            if (name == L"exportname") {
                exportName = Trim(text);
                Say(L"");
            } else if (name == L"packname") {
                packName = Trim(text);
                Say(L"");
            } else if (name == L"fovtext") {
                SetConfig(L"CameraFov", text);
            } else if (name.rfind(L"field:", 0) == 0) {
                Commit(_wtoi(name.c_str() + 6), text);
            }
        }
        Changed();
    }

    bool PickFile(bool save, const wstring& suggested, wstring& out, bool folder = false, bool zip = false) {
        ComPtr<IFileDialog> d;
        if (FAILED(CoCreateInstance(save ? CLSID_FileSaveDialog : CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_PPV_ARGS(&d))))
            return false;
        DWORD o = 0;
        d->GetOptions(&o);
        o |= FOS_FORCEFILESYSTEM;
        if (folder) o |= FOS_PICKFOLDERS;
        else {
            COMDLG_FILTERSPEC f[] = {{L"Riftstone profile (*.json)", L"*.json"}, {L"All files", L"*.*"}};
            COMDLG_FILTERSPEC z[] = {{L"Zip archive (*.zip)", L"*.zip"}, {L"All files", L"*.*"}};
            d->SetFileTypes(2, zip ? z : f);
            d->SetDefaultExtension(zip ? L"zip" : L"json");
            o |= save ? FOS_OVERWRITEPROMPT : FOS_FILEMUSTEXIST;
        }
        d->SetOptions(o);
        if (save) d->SetFileName(suggested.c_str());
        if (folder) d->SetTitle(L"The Dark Arisen folder (the one that holds DDDA.exe)");
        if (FAILED(d->Show(hwnd))) return false;
        ComPtr<IShellItem> item;
        PWSTR p = nullptr;
        if (FAILED(d->GetResult(&item)) || FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &p))) return false;
        out = p;
        CoTaskMemFree(p);
        return true;
    }

    void ChooseFolder() {
        wstring dir;
        if (smoke || !PickFile(false, L"", dir, true)) return;
        dir = NormalPath(dir);
        if (LowerAscii(FileName(dir)) == L"ddda.exe") dir = ParentDir(dir);
        if (!IsGameFolder(dir)) {
            Say(dir + L" has no DDDA.exe and nativePC\\rom: it is not the Dark Arisen folder.", true);
            return;
        }
        game = dir;
        wchar_t local[MAX_PATH];
        if (GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) {
            wstring folder = JoinPath(local, L"Riftstone");
            MakeDirs(folder);
            WritePrivateProfileStringW(L"game", L"folder", game.c_str(), JoinPath(folder, L"studio.ini").c_str());
        }
        Reload();
        Say(L"Using " + game + L".");
    }

    void Export() {
        Profile p;
        wstring error;
        if (!ProfileFromGame(game, exportName, p, error)) {
            Say(error, true);
            return;
        }
        wstring file = smokeSave;
        if (!smoke) {
            wstring suggested = exportName;
            for (auto& ch : suggested)
                if (wcschr(L"<>:\"/\\|?*", ch) || ch < 0x20) ch = L'_';
            if (!PickFile(true, suggested + L".json", file)) return;
        }
        if (file.empty()) return;
        if (!WriteFileAtomic(file, ProfileToJson(p), error)) {
            Say(error, true);
            return;
        }
        Say(L"Wrote " + file + L": " + std::to_wstring(p.features.size()) + L" features, " +
            std::to_wstring(p.valueCount()) + L" settings.");
    }

    void ExportPackNow() {
        wstring file = smokePack;
        if (!smoke) {
            wstring suggested = packName.empty() ? L"Riftstone setup" : packName;
            for (auto& ch : suggested)
                if (wcschr(L"<>:\"/\\|?*", ch) || ch < 0x20) ch = L'_';
            if (!PickFile(true, suggested + L".zip", file, false, true)) return;
        }
        if (file.empty()) return;
        PackReport r;
        wstring error;
        if (!ExportPack(game, packName, file, r, error)) {
            havePack = false;
            Say(error, true);
            return;
        }
        pack = r;
        havePack = true;
        Say(L"Wrote " + file + L": " + std::to_wstring(r.files.size()) +
            L" files. Share the zip; its README tells the player what to do.");
    }

    void Import() {
        wstring file = smokeOpen;
        if (!smoke && !PickFile(false, L"", file)) return;
        std::string raw;
        if (file.empty() || !ReadFileBytes(file, raw, 64u << 20)) {
            Say(L"Cannot read " + file, true);
            return;
        }
        Profile p;
        ApplyReport r;
        wstring error;
        if (!ProfileFromJson(raw, p, error) || !ApplyProfile(game, p, true, r, error)) {
            havePlan = false;
            Say(error, true);
            return;
        }
        plan = p;
        planReport = r;
        planFile = file;
        planSwitches.clear();
        for (const auto& kv : p.features)
            for (const auto& f : feats)
                if (Lower(f.stem) == Lower(kv.first) && f.on != kv.second)
                    planSwitches.push_back((kv.second ? L"turn on " : L"turn off ") + f.title);
        havePlan = true;
        Say(L"");
    }

    void ApplyPlan() {
        ApplyReport r;
        wstring error;
        if (!ApplyProfile(game, plan, false, r, error)) {
            Say(error, true);
            return;
        }
        havePlan = false;
        Reload();
        Say(plan.name + L": switched " + std::to_wstring(r.switched) + L" features, set " + std::to_wstring(r.set) +
            L" settings" + (r.skipped.empty() ? L"." : L", left out " + std::to_wstring(r.skipped.size()) + L"."));
    }

    void FocusStep(int dir) {
        if (ctls.empty()) return;
        int n = int(ctls.size()), at = -1;
        for (int i = 0; i < n; i++)
            if (ctls[size_t(i)].name == focusName) at = i;
        for (int k = 0; k < n; k++) {
            at = (at + dir + n) % n;
            if (ctls[size_t(at)].enabled) break;
        }
        focusName = ctls[size_t(at)].name;
        focusVisible = true;
        EnsureVisible(focusName);
        Changed();
    }

    void Key(WPARAM vk) {
        const Ctl* f = Find(focusName);
        switch (vk) {
        case VK_TAB: FocusStep(GetKeyState(VK_SHIFT) < 0 ? -1 : 1); return;
        case VK_SPACE:
        case VK_RETURN:
            if (f) {
                Ctl c = *f;
                Activate(c);
            }
            return;
        case VK_LEFT:
        case VK_RIGHT:
            if (f && f->kind == C_SLIDER) {
                double step = f->digits ? std::pow(10.0, -f->digits) : 1.0;
                double v = std::clamp(f->cur + (vk == VK_RIGHT ? step : -step), f->lo, f->hi);
                Ctl copy = *f;
                SliderCommit(copy, v);
                Changed();
            } else if (f && f->kind == C_SEGMENT) {
                FocusStep(vk == VK_RIGHT ? 1 : -1);
            }
            return;
        case VK_PRIOR:
        case VK_NEXT:
            Scroll(vk == VK_NEXT ? 300.f : -300.f);
            return;
        }
        if (GetKeyState(VK_CONTROL) < 0 && vk >= '1' && vk < '1' + PAGES) {
            page = int(vk - '1');
            Changed();
        }
    }

    void Scroll(float by) {
        float s = std::clamp(scroll[page] + by, 0.f, maxScroll);
        if (s != scroll[page]) {
            EndEdit(true);
            scroll[page] = s;
            Changed();
        }
    }

    void MouseDown(float x, float y) {
        EndEdit(true);
        focusVisible = false;
        const Ctl* c = Hit(x, y);
        pressedName = c ? c->name : L"";
        if (c) focusName = c->name;
        if (c && c->kind == C_SLIDER && c->enabled) {
            dragging = true;
            dragName = c->name;
            dragValue = SliderAt(*c, x);
            if (hwnd) SetCapture(hwnd);
        }
        Changed();
    }

    void MouseMove(float x, float y) {
        if (dragging) {
            const Ctl* c = Find(dragName);
            if (c) dragValue = SliderAt(*c, x);
            Changed();
            return;
        }
        const Ctl* c = Hit(x, y);
        wstring now = c && c->enabled ? c->name : L"";
        if (now != hotName) {
            hotName = now;
            Changed();
        }
        if (!tracking && hwnd) {
            TRACKMOUSEEVENT t{sizeof t, TME_LEAVE, hwnd, 0};
            tracking = TrackMouseEvent(&t) != FALSE;
        }
    }

    void MouseUp(float x, float y) {
        if (dragging) {
            dragging = false;
            if (hwnd && GetCapture() == hwnd) ReleaseCapture();
            const Ctl* c = Find(dragName);
            if (c) {
                Ctl copy = *c;
                SliderCommit(copy, dragValue);
            }
            dragName.clear();
            pressedName.clear();
            Changed();
            return;
        }
        const Ctl* c = Hit(x, y);
        bool same = c && c->name == pressedName;
        pressedName.clear();
        if (same) {
            Ctl copy = *c;
            Activate(copy);
        } else Changed();
    }

    void Resize() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        W = Dip(rc.right);
        H = Dip(rc.bottom);
        if (rt) rt->Resize(D2D1::SizeU(UINT(std::max<LONG>(rc.right, 1)), UINT(std::max<LONG>(rc.bottom, 1))));
        EndEdit(true);
        Changed();
    }

    LRESULT Proc(UINT m, WPARAM w, LPARAM l) {
        switch (m) {
        case WM_CREATE: {
            dpi = float(GetDpiForWindow(hwnd));
            BOOL dark = TRUE;
            DwmSetWindowAttribute(hwnd, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &dark, sizeof dark);
            COLORREF caption = RGB(0x08, 0x08, 0x0c), border = RGB(0x28, 0x2d, 0x39);
            DwmSetWindowAttribute(hwnd, 35 /* DWMWA_CAPTION_COLOR */, &caption, sizeof caption);
            DwmSetWindowAttribute(hwnd, 34 /* DWMWA_BORDER_COLOR */, &border, sizeof border);
            Reload();
            Resize();
            if (!smoke) {
                SetTimer(hwnd, TIMER_POLL, 2000, nullptr);
                SetTimer(hwnd, TIMER_LIVE, 1000, nullptr);
            }
            return 0;
        }
        case WM_SIZE: Resize(); return 0;
        case WM_PAINT: OnPaint(); return 0;
        case WM_ERASEBKGND: return 1;
        case WM_DPICHANGED: {
            dpi = float(HIWORD(w));
            const RECT* r = reinterpret_cast<const RECT*>(l);
            if (rt) rt->SetDpi(dpi, dpi);
            if (editFont) {
                EndEdit(true);
                DeleteObject(editFont);
                editFont = nullptr;
            }
            SetWindowPos(hwnd, nullptr, r->left, r->top, r->right - r->left, r->bottom - r->top,
                         SWP_NOZORDER | SWP_NOACTIVATE);
            Resize();
            return 0;
        }
        case WM_GETMINMAXINFO: {
            auto* mm = reinterpret_cast<MINMAXINFO*>(l);
            mm->ptMinTrackSize = {LONG(Px(940)), LONG(Px(620))};
            return 0;
        }
        case WM_MOUSEMOVE: MouseMove(Dip(GET_X_LPARAM(l)), Dip(GET_Y_LPARAM(l))); return 0;
        case WM_MOUSELEAVE:
            tracking = false;
            if (!hotName.empty()) {
                hotName.clear();
                Changed();
            }
            return 0;
        case WM_LBUTTONDOWN: MouseDown(Dip(GET_X_LPARAM(l)), Dip(GET_Y_LPARAM(l))); return 0;
        case WM_LBUTTONUP: MouseUp(Dip(GET_X_LPARAM(l)), Dip(GET_Y_LPARAM(l))); return 0;
        case WM_MOUSEWHEEL: Scroll(-float(GET_WHEEL_DELTA_WPARAM(w)) / WHEEL_DELTA * 56.f); return 0;
        case WM_KEYDOWN: Key(w); return 0;
        case WM_SETCURSOR:
            if (LOWORD(l) == HTCLIENT) {
                SetCursor(LoadCursorW(nullptr, hotName.empty() ? IDC_ARROW : IDC_HAND));
                return TRUE;
            }
            break;
        case WM_TIMER:
            if (w == TIMER_POLL && !dragging && !edit) {
                bool now = GameRunning();
                SessionInfo s = game.empty() ? SessionInfo() : ReadSession(game);
                if (now != running || s.started != session.started || s.safeMode != session.safeMode ||
                    s.newestReport != session.newestReport) {
                    Reload();
                    Changed();
                }
            }
            if (w == TIMER_LIVE && page == LIVE && !dragging && !edit) {   // the live page, once a second
                haveLive = ReadLive(0, live, liveName);
                Changed();
            }
            return 0;
        case WM_CTLCOLOREDIT: {
            HDC dc = HDC(w);
            SetTextColor(dc, RGB(0xe2, 0xe8, 0xf0));
            SetBkColor(dc, RGB(0x0c, 0x0d, 0x13));
            return LRESULT(editBrush);
        }
        case WM_APP_ENDEDIT: EndEdit(w != 0); return 0;
        case WM_DESTROY:
            KillTimer(hwnd, TIMER_POLL);
            KillTimer(hwnd, TIMER_LIVE);
            if (editFont) DeleteObject(editFont);
            if (editBrush) DeleteObject(editBrush);
            editFont = nullptr;
            editBrush = nullptr;
            rt.Reset();
            if (!smoke) PostQuitMessage(0);
            return 0;
        }
        return DefWindowProcW(hwnd, m, w, l);
    }
};

LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    App* a = reinterpret_cast<App*>(GetWindowLongPtrW(h, GWLP_USERDATA));
    if (m == WM_NCCREATE) {
        a = static_cast<App*>(reinterpret_cast<CREATESTRUCTW*>(l)->lpCreateParams);
        SetWindowLongPtrW(h, GWLP_USERDATA, LONG_PTR(a));
        a->hwnd = h;
    }
    return a ? a->Proc(m, w, l) : DefWindowProcW(h, m, w, l);
}

const wchar_t* const kClass = L"RiftstoneStudio";

bool Register(HINSTANCE inst) {
    WNDCLASSEXW wc{sizeof wc};
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = CreateSolidBrush(RGB(0x08, 0x08, 0x0c));
    wc.lpszClassName = kClass;
    return RegisterClassExW(&wc) || GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
}

HWND Create(App& a, HINSTANCE inst, int wPx, int hPx) {
    a.inst = inst;
    return CreateWindowExW(0, kClass, L"Riftstone Studio", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN, CW_USEDEFAULT,
                           CW_USEDEFAULT, wPx, hPx, nullptr, nullptr, inst, &a);
}

// The mark as a window icon: drawn into a bitmap, its alpha straightened as icons keep it.
HICON MakeIcon(int size) {
    ComPtr<IWICImagingFactory> wic;
    ComPtr<IWICBitmap> bmp;
    ComPtr<ID2D1RenderTarget> t;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&wic))) ||
        FAILED(wic->CreateBitmap(UINT(size), UINT(size), GUID_WICPixelFormat32bppPBGRA, WICBitmapCacheOnLoad, &bmp)) ||
        FAILED(g.d2d->CreateWicBitmapRenderTarget(
            bmp.Get(), D2D1::RenderTargetProperties(D2D1_RENDER_TARGET_TYPE_SOFTWARE,
                                                    D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED),
                                                    96, 96),
            &t)))
        return nullptr;
    t->BeginDraw();
    t->Clear(D2D1::ColorF(0, 0.f));
    DrawMark(t.Get(), {0, 0, float(size), float(size)});
    if (FAILED(t->EndDraw())) return nullptr;
    std::vector<uint8_t> px(size_t(size) * size_t(size) * 4);
    if (FAILED(bmp->CopyPixels(nullptr, UINT(size) * 4, UINT(px.size()), px.data()))) return nullptr;
    for (size_t i = 0; i < px.size(); i += 4) {
        uint8_t a = px[i + 3];
        if (a && a < 255)
            for (int k = 0; k < 3; k++) px[i + size_t(k)] = uint8_t(std::min(255, px[i + size_t(k)] * 255 / a));
    }
    BITMAPV5HEADER bi{};
    bi.bV5Size = sizeof bi;
    bi.bV5Width = size;
    bi.bV5Height = -size;
    bi.bV5Planes = 1;
    bi.bV5BitCount = 32;
    bi.bV5Compression = BI_BITFIELDS;
    bi.bV5RedMask = 0x00FF0000;
    bi.bV5GreenMask = 0x0000FF00;
    bi.bV5BlueMask = 0x000000FF;
    bi.bV5AlphaMask = 0xFF000000;
    void* bits = nullptr;
    HDC dc = GetDC(nullptr);
    HBITMAP color = CreateDIBSection(dc, reinterpret_cast<BITMAPINFO*>(&bi), DIB_RGB_COLORS, &bits, nullptr, 0);
    ReleaseDC(nullptr, dc);
    if (!color) return nullptr;
    memcpy(bits, px.data(), px.size());
    HBITMAP mask = CreateBitmap(size, size, 1, 1, nullptr);
    ICONINFO ii{TRUE, 0, 0, mask, color};
    HICON icon = CreateIconIndirect(&ii);
    DeleteObject(color);
    DeleteObject(mask);
    return icon;
}

void Pump() {
    MSG msg;
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

// The page drawn into a bitmap (the screenshot and the smoke test's draw check).
bool PaintToBitmap(App& a, int wPx, int hPx, const wstring& png, wstring& log) {
    ComPtr<IWICImagingFactory> wic;
    ComPtr<IWICBitmap> bmp;
    ComPtr<ID2D1RenderTarget> t;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&wic))) ||
        FAILED(wic->CreateBitmap(UINT(wPx), UINT(hPx), GUID_WICPixelFormat32bppPBGRA, WICBitmapCacheOnLoad, &bmp)) ||
        FAILED(g.d2d->CreateWicBitmapRenderTarget(
            bmp.Get(), D2D1::RenderTargetProperties(D2D1_RENDER_TARGET_TYPE_DEFAULT,
                                                    D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED),
                                                    a.dpi, a.dpi),
            &t))) {
        log += L"no bitmap to draw into\n";
        return false;
    }
    t->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
    Res saved = a.res;
    if (!a.res.Make(t.Get())) {
        log += L"no brushes\n";
        return false;
    }
    t->BeginDraw();
    a.Paint(t.Get());
    HRESULT hr = t->EndDraw();
    a.res = saved;
    if (FAILED(hr)) {
        log += L"drawing failed\n";
        return false;
    }
    if (png.empty()) return true;
    ComPtr<IWICStream> stream;
    ComPtr<IWICBitmapEncoder> enc;
    ComPtr<IWICBitmapFrameEncode> frame;
    if (FAILED(wic->CreateStream(&stream)) || FAILED(stream->InitializeFromFilename(png.c_str(), GENERIC_WRITE)) ||
        FAILED(wic->CreateEncoder(GUID_ContainerFormatPng, nullptr, &enc)) ||
        FAILED(enc->Initialize(stream.Get(), WICBitmapEncoderNoCache)) || FAILED(enc->CreateNewFrame(&frame, nullptr)) ||
        FAILED(frame->Initialize(nullptr)) || FAILED(frame->SetSize(UINT(wPx), UINT(hPx))) ||
        FAILED(frame->WriteSource(bmp.Get(), nullptr)) || FAILED(frame->Commit()) || FAILED(enc->Commit())) {
        log += L"cannot write " + png + L"\n";
        return false;
    }
    return true;
}

struct Com {
    bool ok;
    Com() : ok(SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE))) {}
    ~Com() {
        if (ok) CoUninitialize();
    }
};

}  // namespace

int RunStudioWindow(HINSTANCE inst, const std::wstring& game, int show) {
    Com com;
    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);
    if (!g.Init() || !Register(inst)) {
        MessageBoxW(nullptr, L"Riftstone Studio needs Direct2D (Windows 10 or later).", L"Riftstone Studio", MB_ICONERROR);
        return 1;
    }
    App app;
    app.game = game;
    UINT sys = GetDpiForSystem();
    HWND hwnd = Create(app, inst, MulDiv(1180, int(sys), 96), MulDiv(780, int(sys), 96));
    if (!hwnd) return 1;
    int big = GetSystemMetricsForDpi(SM_CXICON, UINT(app.dpi)), little = GetSystemMetricsForDpi(SM_CXSMICON, UINT(app.dpi));
    HICON iconBig = MakeIcon(big), iconSmall = MakeIcon(little);
    if (iconBig) SendMessageW(hwnd, WM_SETICON, ICON_BIG, LPARAM(iconBig));
    if (iconSmall) SendMessageW(hwnd, WM_SETICON, ICON_SMALL, LPARAM(iconSmall));
    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    if (iconBig) DestroyIcon(iconBig);
    if (iconSmall) DestroyIcon(iconSmall);
    return int(msg.wParam);
}

// The Graphics page on a config.ini of the smoke's own (LOCALAPPDATA pointed at a temporary folder, so the player's
// is never read or written); enemy_cap and collision_cap are left as the steps found them.
template <class Step, class Click, class Drawn>
void SmokeGraphics(App& app, const wstring& game, Step& step, Click& click, Drawn& drawn) {
    wchar_t old[32768];
    DWORD had = GetEnvironmentVariableW(L"LOCALAPPDATA", old, 32768);
    wchar_t tmp[MAX_PATH + 1];
    GetTempPathW(MAX_PATH + 1, tmp);
    wstring local = JoinPath(tmp, L"riftstone-studio-smoke-" + std::to_wstring(GetCurrentProcessId()));
    wstring dir = JoinPath(local, L"CAPCOM\\DRAGONS DOGMA DARK ARISEN"), cfg = JoinPath(dir, L"config.ini");
    wstring error;
    MakeDirs(dir);
    WriteFileAtomic(cfg, "[GRAPHICS]\r\nResolution=1920x1080\r\nCameraFov=0.000000\r\nViewRange=NORMAL\r\n", error);
    SetEnvironmentVariableW(L"LOCALAPPDATA", local.c_str());
    wstring enb = JoinPath(game, L"enbseries.ini");
    bool ownEnb = !FileExists(enb) && WriteFileAtomic(enb, "[GLOBAL]\r\nUseEffect=true\r\n", error);
    step(click(L"nav:graphics") && app.page == GRAPHICS && app.gfx.configFound, L"Graphics opens on a stand-in config.ini");
    drawn(L"graphics");
    std::string text;
    const Ctl* fov = app.Find(L"fov");
    float width = fov ? fov->r.right - fov->r.left : 0;
    step(fov && click(L"fov", width - 1) && ReadFileBytes(cfg, text) && text.find("CameraFov=15.000000\r\n") != std::string::npos,
         L"the field of view slider dragged to its end wrote CameraFov=15.000000");
    step(click(L"vr:FAR") && ReadFileBytes(cfg, text) && text.find("ViewRange=FAR\r\n") != std::string::npos,
         L"View range FAR written");
    if (ownEnb) {
        step(click(L"enb") && ReadFileBytes(enb, text) && text.find("UseEffect=false") != std::string::npos,
             L"ENB's switch wrote UseEffect=false");
        DeleteFileW(enb.c_str());
    }
    struct Kept {
        const wchar_t* stem;
        bool had, on;
        std::string ini;
    };
    Kept kept[] = {{L"enemy_cap", false, false, ""}, {L"collision_cap", false, false, ""}};
    for (auto& k : kept)
        for (const auto& f : ListFeatures(game))
            if (Lower(f.stem) == k.stem) {
                k.had = true;
                k.on = f.on;
                ReadFileBytes(f.ini(), k.ini);
            }
    if (kept[0].had) {
        step(click(L"crowd:64") && app.gfx.enemyCapOn && app.gfx.slots == 64, L"64 at once: enemy_cap on, 64 slots");
        step(click(L"crowd:30") && app.gfx.slots == 30, L"Back to 30");
        for (const auto& k : kept) {
            if (!k.had) continue;
            bool changed = false;
            Toggle(game, k.stem, k.on, changed, error);
            for (const auto& f : ListFeatures(game))
                if (Lower(f.stem) == k.stem && !k.ini.empty()) WriteFileAtomic(f.ini(), k.ini, error);
        }
        app.Reload();
    }
    DeleteFileW(cfg.c_str());
    for (wstring d = dir; d.size() > local.size(); d = ParentDir(d)) RemoveDirectoryW(d.c_str());
    RemoveDirectoryW(local.c_str());
    SetEnvironmentVariableW(L"LOCALAPPDATA", had && had < 32768 ? old : nullptr);
    app.gfx = ReadGraphics(game);
}

// A live-stats page as the loader publishes it, in a mapping of the smoke's own name.
void PutLive(uint8_t* v) {
    auto u32 = [&](size_t o, uint32_t x) { memcpy(v + o, &x, 4); };
    auto u64 = [&](size_t o, uint64_t x) { memcpy(v + o, &x, 8); };
    memcpy(v, "RSLIVE1", 8);
    u32(0x008, 1);
    u32(0x00C, 0x1000);
    u32(0x010, GetCurrentProcessId());
    u32(0x014, 1);
    u32(0x01C, 1 | 4);
    u32(0x020, 2);
    u32(0x024, 125000);
    u64(0x030, 4ull << 30);
    u64(0x038, 3ull << 30);
    u64(0x040, 512ull << 20);
    u64(0x048, (3ull << 30) + (100ull << 20));
    u64(0x050, 300ull << 20);
    u64(0x058, 2ull << 30);
    u32(0x06C, 300);
    u32(0x070, 6060);
    u32(0x074, 6060);
    u32(0x078, 9000);
    u32(0x07C, 20000);
    u32(0x080, 2);
    u32(0x09C, 12);
    u32(0x0A0, 30);
    u32(0x0A4, 30);
    u32(0x0A8, 2560);
    u32(0x0AC, 1440);
    u32(0x0B4, 165);
    u32(0x0B8, 44);
    for (uint32_t i = 0; i < 256; i++) u32(0x0C0 + 4 * i, i % 97 == 5 ? 21000 : 5600 + (i * 37) % 900);
    u32(0x9D8, 100);
    u64(0x9E4, (3ull << 30) + (200ull << 20));
    u32(0xA14, 1);
    memcpy(v + 0x4C0, "1.0.5", 6);
    const char plugins[] = "enemy_cap.dll=loaded;collision_cap.dll=loaded;free_sprint.dll=loaded";
    memcpy(v + 0x6D8, plugins, sizeof plugins);
}

template <class Step, class Click, class Drawn>
void SmokeLive(App& app, Step& step, Click& click, Drawn& drawn) {
    wstring name = L"Local\\RiftstoneStudioSmoke-" + std::to_wstring(GetCurrentProcessId());
    HANDLE map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, 0x1000, name.c_str());
    uint8_t* v = map ? static_cast<uint8_t*>(MapViewOfFile(map, FILE_MAP_WRITE, 0, 0, 0x1000)) : nullptr;
    if (v) PutLive(v);
    app.liveName = name;
    step(v && click(L"nav:live") && app.page == LIVE && app.haveLive && app.live.stage == 100 &&
             app.live.frameTimesUs.size() == 256,
         L"Live reads a stand-in page");
    drawn(L"live");
    step(click(L"snapshot") && app.statusBad, L"Take a snapshot asks the process and says when it takes none");
    if (v) UnmapViewOfFile(v);
    if (map) CloseHandle(map);
    app.liveName.clear();
    app.haveLive = ReadLive(0, app.live, L"");
}

int RunGuiSmoke(HINSTANCE inst, const std::wstring& game, const std::wstring& profileOut, std::wstring& log) {
    Com com;
    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);
    if (!g.Init() || !Register(inst)) {
        log = L"Direct2D did not start";
        return 1;
    }
    App app;
    app.game = game;
    app.smoke = true;
    app.smokeSave = profileOut;
    app.smokeOpen = profileOut;
    HWND hwnd = Create(app, inst, 1180, 780);   // never shown
    if (!hwnd) {
        log = L"the window was not made";
        return 1;
    }
    int failed = 0;
    auto step = [&](bool ok, const wstring& what) {
        if (!ok) failed++;
        log += wstring(ok ? L"" : L"FAILED: ") + what + L"; ";
    };
    auto click = [&](const wstring& name, float atX = -1) {
        app.EnsureVisible(name);
        const Ctl* c = app.Find(name);
        if (!c) return false;
        float x = atX >= 0 ? c->r.left + atX : (c->r.left + c->r.right) / 2, y = (c->r.top + c->r.bottom) / 2;
        LPARAM lp = MAKELPARAM(int(std::lround(app.Px(x))), int(std::lround(app.Px(y))));
        SendMessageW(hwnd, WM_MOUSEMOVE, 0, lp);
        SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp);
        SendMessageW(hwnd, WM_LBUTTONUP, 0, lp);
        Pump();
        return true;
    };
    auto drawn = [&](const wchar_t* what) {
        wstring l;
        bool ok = PaintToBitmap(app, int(app.Px(app.W)), int(app.Px(app.H)), L"", l);
        step(ok, wstring(L"the ") + what + L" page draws" + (ok ? L"" : L" (" + l + L")"));
    };
    drawn(L"home");
    step(click(L"nav:features") && app.page == FEATURES, L"Features opens");
    drawn(L"features");
    bool wasOn = false;
    for (const auto& f : app.feats)
        if (f.stem == L"enemy_cap") wasOn = f.on;
    step(click(L"toggle:enemy_cap"), L"the enemy_cap switch is there");
    bool nowOn = wasOn;
    for (const auto& f : ListFeatures(game))
        if (f.stem == L"enemy_cap") nowOn = f.on;
    step(nowOn != wasOn, L"clicking it switched enemy_cap");
    step(click(L"nav:settings") && app.page == SETTINGS, L"Settings opens");
    step(click(L"stem:enemy_cap") && app.selStem == L"enemy_cap", L"enemy_cap's settings open");
    int slots = -1;
    for (size_t i = 0; i < app.settings.size(); i++)
        if (Lower(app.settings[i].key) == L"slots") slots = int(i);
    step(slots >= 0 && click(L"slider:" + std::to_wstring(slots), 0.f), L"the slots slider is dragged to its start");
    std::vector<Setting> now;
    for (const auto& f : ListFeatures(game))
        if (f.stem == L"enemy_cap") ReadSettings(f.ini(), now);
    step(!now.empty() && now[0].value == L"10", L"the slider wrote slots = 10");
    drawn(L"settings");
    if (click(L"stem:free_sprint")) {
        int mode = -1;
        for (size_t i = 0; i < app.settings.size(); i++)
            if (Lower(app.settings[i].key) == L"mode") mode = int(i);
        step(mode >= 0 && click(L"seg:" + std::to_wstring(mode) + L":always"), L"free_sprint's mode has its choices");
        step(mode >= 0 && app.settings[size_t(mode)].value == L"always", L"choosing one wrote mode = always");
    }
    step(click(L"nav:profiles") && app.page == PROFILES, L"Profiles opens");
    step(click(L"exportname") && app.edit != nullptr, L"the profile name field opens for typing");
    if (app.edit) {
        SetWindowTextW(app.edit, L"From the window");
        SendMessageW(app.edit, WM_KEYDOWN, VK_RETURN, 0);
        Pump();
    }
    step(app.edit == nullptr && app.exportName == L"From the window", L"Enter keeps the typed name");
    DeleteFileW(profileOut.c_str());
    step(click(L"export") && FileExists(profileOut), L"Export writes the profile");
    std::string raw;
    Profile p;
    wstring error;
    step(ReadFileBytes(profileOut, raw) && ProfileFromJson(raw, p, error) && p.name == L"From the window",
         L"the exported profile reads back under its name");
    step(click(L"import") && app.havePlan, L"Import shows its plan");
    drawn(L"profiles");
    step(click(L"cancel") && !app.havePlan, L"Cancel drops the plan");
    SmokeGraphics(app, game, step, click, drawn);
    SmokeLive(app, step, click, drawn);
    step(click(L"nav:pack") && app.page == PACK, L"Mod pack opens");
    step(click(L"packname") && app.edit != nullptr, L"the pack's name field opens for typing");
    if (app.edit) {
        SetWindowTextW(app.edit, L"From the window");
        SendMessageW(app.edit, WM_KEYDOWN, VK_RETURN, 0);
        Pump();
    }
    step(app.edit == nullptr && app.packName == L"From the window", L"Enter keeps the pack's name");
    wstring zip = profileOut + L".zip";
    app.smokePack = zip;
    DeleteFileW(zip.c_str());
    bool clicked = click(L"pack");
    if (app.loaderVer.empty()) step(clicked && !FileExists(zip), L"with no loader in the folder there is no pack");
    else step(clicked && FileExists(zip) && app.havePack && !app.pack.files.empty(), L"Export writes the pack");
    drawn(L"pack");
    step(click(L"nav:home") && app.page == HOME, L"Home opens");
    DestroyWindow(hwnd);
    Pump();
    log += std::to_wstring(failed) + L" failed";
    return failed ? 1 : 0;
}

int RenderPage(HINSTANCE inst, const std::wstring& game, const std::wstring& page, const std::wstring& png, int width,
               int height, int dpi, std::wstring& log) {
    (void)inst;
    Com com;
    if (!g.Init()) {
        log = L"Direct2D did not start";
        return 1;
    }
    App app;
    app.game = game;
    app.smoke = true;
    app.dpi = float(dpi);
    app.W = app.Dip(width);
    app.H = app.Dip(height);
    // PAGE is home, features, settings[/STEM], profiles, import/FILE (Profiles with that file's plan), graphics,
    // live[/MAPPING] (a stand-in live-stats mapping by name), pack[/ZIP] (the pack written to ZIP, and its report)
    wstring name = page, arg;
    size_t slash = page.find(L'/');
    if (slash != wstring::npos) {
        name = page.substr(0, slash);
        arg = page.substr(slash + 1);
    }
    if (name == L"live") app.liveName = arg;
    app.Reload();
    if (name == L"pack" && !arg.empty()) {
        app.smokePack = arg;
        app.ExportPackNow();
    }
    for (int p = 0; p < PAGES; p++)
        if (name == kPageKey[p]) app.page = p;
    if (name == L"settings" && !arg.empty()) {
        app.selStem = arg;
        app.LoadSettings();
    }
    if (name == L"import") {
        app.page = PROFILES;
        app.smokeOpen = arg;
        app.Import();
    }
    app.Build();
    return PaintToBitmap(app, width, height, png, log) ? 0 : 1;
}
