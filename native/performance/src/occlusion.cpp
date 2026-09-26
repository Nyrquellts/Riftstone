#include "occlusion.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace riftstone::perf {
namespace {
void dimensions(std::uint32_t w, std::uint32_t h) {
    if (!w || !h || w > 16384 || h > 16384 || std::uint64_t(w)*h > 16777216)
        throw std::invalid_argument("depth dimensions outside bounded allocation contract");
}
bool usable(const ClipVertex& v) {
    return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z) && std::isfinite(v.w)
        && v.w > 0.000001 && v.z >= 0 && v.z <= v.w
        && std::abs(v.x/v.w) <= 1000000 && std::abs(v.y/v.w) <= 1000000;
}
double edge(double ax,double ay,double bx,double by,double x,double y) {
    return (bx-ax)*(y-ay)-(by-ay)*(x-ax);
}
}
ScreenRect project_bounds(const Aabb& box,const Mat4& matrix,std::uint32_t w,std::uint32_t h) noexcept {
    ScreenRect out{};
    if (!w || !h || w > 16384 || h > 16384) return out;
    for (unsigned i=0;i<3;++i)
        if (!std::isfinite(box.minimum[i]) || !std::isfinite(box.maximum[i]) || box.minimum[i]>box.maximum[i]) return out;
    for (double v:matrix.row_major) if (!std::isfinite(v)) return out;
    out.min_x=out.min_y=out.nearest_depth=std::numeric_limits<double>::infinity();
    out.max_x=out.max_y=-std::numeric_limits<double>::infinity();
    for (unsigned corner=0;corner<8;++corner) {
        const double p[4]={corner&1 ? box.maximum[0]:box.minimum[0],
            corner&2 ? box.maximum[1]:box.minimum[1],corner&4 ? box.maximum[2]:box.minimum[2],1};
        double c[4]{},error[4]{};
        for (unsigned row=0;row<4;++row) {
            double magnitude=0;
            for (unsigned col=0;col<4;++col) {const double product=matrix.row_major[row*4+col]*p[col];c[row]+=product;magnitude+=std::abs(product);}
            error[row]=16*std::numeric_limits<double>::epsilon()*magnitude+16*std::numeric_limits<double>::denorm_min();
            if (!std::isfinite(c[row]) || !std::isfinite(error[row])) return {};
        }
        const double wlo=c[3]-error[3],whi=c[3]+error[3];
        if (wlo<=0.000001 || c[2]-error[2]<=DepthPyramid::depth_bias*whi || c[2]+error[2]>wlo) return {};
        double lo[3],hi[3];
        for(unsigned axis=0;axis<3;++axis) {
            const double a=(c[axis]-error[axis])/wlo,b=(c[axis]-error[axis])/whi;
            const double d=(c[axis]+error[axis])/wlo,e=(c[axis]+error[axis])/whi;
            lo[axis]=std::nextafter(std::min({a,b,d,e}),-std::numeric_limits<double>::infinity());
            hi[axis]=std::nextafter(std::max({a,b,d,e}),std::numeric_limits<double>::infinity());
            if(!std::isfinite(lo[axis])||!std::isfinite(hi[axis])||std::abs(lo[axis])>1000000||std::abs(hi[axis])>1000000)return {};
        }
        out.min_x=std::min(out.min_x,(lo[0]*0.5+0.5)*w);out.max_x=std::max(out.max_x,(hi[0]*0.5+0.5)*w);
        out.min_y=std::min(out.min_y,(0.5-hi[1]*0.5)*h);out.max_y=std::max(out.max_y,(0.5-lo[1]*0.5)*h);
        out.nearest_depth=std::min(out.nearest_depth,lo[2]);
    }
    out.valid=true;
    return out;
}
void DepthPyramid::build(std::uint32_t w,std::uint32_t h,std::span<const float> input) {
    dimensions(w,h);
    if (input.size()!=std::size_t(w)*h) throw std::invalid_argument("depth input size mismatch");
    std::vector<DepthLevel> next;
    next.push_back({w,h,std::vector<float>(input.begin(),input.end())});
    for (float& z:next.front().depth) if (!std::isfinite(z) || z<0 || z>1) z=1;
    while (w>1 || h>1) {
        const auto nw=(w+1)/2, nh=(h+1)/2;
        DepthLevel level{nw,nh,std::vector<float>(std::size_t(nw)*nh,0)};
        const auto& source=next.back().depth;
        for (std::uint32_t y=0;y<nh;++y) for (std::uint32_t x=0;x<nw;++x) {
            float maximum=0;
            for (unsigned dy=0;dy<2;++dy) for (unsigned dx=0;dx<2;++dx)
                if (2*x+dx<w && 2*y+dy<h) maximum=std::max(maximum,source[std::size_t(2*y+dy)*w+2*x+dx]);
            level.depth[std::size_t(y)*nw+x]=maximum;
        }
        next.push_back(std::move(level)); w=nw; h=nh;
    }
    levels_=std::move(next);
}
void DepthPyramid::rasterize(std::uint32_t w,std::uint32_t h,std::span<const ClipTriangle> triangles) {
    dimensions(w,h);
    std::vector<float> depth(std::size_t(w)*h,1);
    for (const auto& triangle:triangles) {
        double x[3],y[3],far_z=0;
        bool valid=true;
        for (unsigned i=0;i<3;++i) {
            const auto& v=triangle.vertices[i];
            if (!usable(v)) { valid=false; break; }
            x[i]=(v.x/v.w*0.5+0.5)*w; y[i]=(0.5-v.y/v.w*0.5)*h;
            far_z=std::max(far_z,v.z/v.w);
        }
        if (!valid) continue;
        double area=edge(x[0],y[0],x[1],y[1],x[2],y[2]);
        if (!std::isfinite(area) || std::abs(area)<1e-9) continue;
        if (area<0) { std::swap(x[1],x[2]); std::swap(y[1],y[2]); area=-area; }
        const double scale=std::max({1.0,std::abs(x[0]),std::abs(x[1]),std::abs(x[2]),std::abs(y[0]),std::abs(y[1]),std::abs(y[2])});
        const double tolerance=1e-7*(1+area)+128*std::numeric_limits<double>::epsilon()*scale*scale;
        const int left=static_cast<int>(std::clamp(std::floor(std::min({x[0],x[1],x[2]})),0.0,double(w)));
        const int right=static_cast<int>(std::clamp(std::ceil(std::max({x[0],x[1],x[2]})),0.0,double(w)));
        const int top=static_cast<int>(std::clamp(std::floor(std::min({y[0],y[1],y[2]})),0.0,double(h)));
        const int bottom=static_cast<int>(std::clamp(std::ceil(std::max({y[0],y[1],y[2]})),0.0,double(h)));
        const float conservative_z=std::min(1.0F,std::nextafter(static_cast<float>(far_z)+depth_bias,1.0F));
        for (int py=top;py<bottom;++py) for (int px=left;px<right;++px) {
            bool covered=true;
            for (unsigned e=0;e<3 && covered;++e) for (unsigned corner=0;corner<4;++corner) {
                if (edge(x[e],y[e],x[(e+1)%3],y[(e+1)%3],double(px)+(corner&1),double(py)+((corner>>1)&1))<=tolerance) {
                    covered=false; break;
                }
            }
            if (covered) {
                auto& cell=depth[std::size_t(py)*w+static_cast<unsigned>(px)];
                cell=std::min(cell,conservative_z);
            }
        }
    }
    build(w,h,depth);
}
bool DepthPyramid::occluded(const ScreenRect& b) const noexcept {
    if (levels_.empty() || !b.valid || !std::isfinite(b.min_x) || !std::isfinite(b.min_y)
        || !std::isfinite(b.max_x) || !std::isfinite(b.max_y) || !std::isfinite(b.nearest_depth)
        || b.nearest_depth<=depth_bias || b.nearest_depth>1 || b.min_x>=b.max_x || b.min_y>=b.max_y) return false;
    const auto& base=levels_.front();
    if (b.max_x<=0 || b.max_y<=0 || b.min_x>=base.width || b.min_y>=base.height) return false;
    // Expand by one complete cell to cover rounding and touching boundaries.
    std::uint32_t x0=static_cast<std::uint32_t>(std::clamp(std::floor(b.min_x)-1,0.0,double(base.width-1)));
    std::uint32_t y0=static_cast<std::uint32_t>(std::clamp(std::floor(b.min_y)-1,0.0,double(base.height-1)));
    std::uint32_t x1=static_cast<std::uint32_t>(std::clamp(std::ceil(b.max_x),0.0,double(base.width-1)));
    std::uint32_t y1=static_cast<std::uint32_t>(std::clamp(std::ceil(b.max_y),0.0,double(base.height-1)));
    std::size_t level=0;
    while (level+1<levels_.size() && std::max(x1-x0,y1-y0)>4) { x0/=2;y0/=2;x1/=2;y1/=2;++level; }
    const auto& chosen=levels_[level];
    for (auto y=y0;y<=y1;++y) for (auto x=x0;x<=x1;++x)
        if (!(b.nearest_depth-depth_bias>chosen.depth[std::size_t(y)*chosen.width+x])) return false;
    return true;
}
}
