#include "occlusion.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>

using namespace riftstone::perf;
namespace {
std::uint64_t checks=0,queries=0,scenes=0;
void check(bool value,const char* message) { ++checks;if (!value) throw std::runtime_error(message); }
bool scalar(const DepthPyramid& p,const ScreenRect& r) {
    if (p.levels().empty() || !r.valid || !std::isfinite(r.min_x) || !std::isfinite(r.min_y)
        || !std::isfinite(r.max_x) || !std::isfinite(r.max_y) || !std::isfinite(r.nearest_depth)
        || r.nearest_depth<=DepthPyramid::depth_bias || r.nearest_depth>1 || r.min_x>=r.max_x || r.min_y>=r.max_y) return false;
    const auto& b=p.levels()[0];
    if (r.max_x<=0 || r.max_y<=0 || r.min_x>=b.width || r.min_y>=b.height) return false;
    // Deliberately scans every original cell, with no hierarchy or mip coordinates.
    const auto left=std::uint32_t(std::clamp(std::floor(r.min_x)-1,0.0,double(b.width-1)));
    const auto right=std::uint32_t(std::clamp(std::ceil(r.max_x),0.0,double(b.width-1)));
    const auto top=std::uint32_t(std::clamp(std::floor(r.min_y)-1,0.0,double(b.height-1)));
    const auto bottom=std::uint32_t(std::clamp(std::ceil(r.max_y),0.0,double(b.height-1)));
    for (auto y=top;y<=bottom;++y) for (auto x=left;x<=right;++x) {
        if (!(r.nearest_depth-DepthPyramid::depth_bias>b.depth[std::size_t(y)*b.width+x])) return false;
    }
    return true;
}
ClipVertex vertex(double x,double y,double z,double w=1) { return {x*w,y*w,z*w,w}; }
void fixed_tests() {
    DepthPyramid p;
    check(!p.occluded({1,1,3,3,.8,true}),"empty pyramid culled");
    bool rejected=false;try {p.build(0,1,{});} catch(const std::invalid_argument&) {rejected=true;}
    check(rejected,"zero dimension accepted");
    rejected=false;try {p.build(2,2,std::vector<float>(3));} catch(const std::invalid_argument&) {rejected=true;}
    check(rejected,"incorrect buffer accepted");
    p.build(17,13,std::vector<float>(221,.25F));
    check(p.levels().back().width==1 && p.levels().back().height==1,"odd hierarchy incomplete");
    check(p.occluded({3,3,8,8,.75,true}),"opaque depth did not cull");
    check(!p.occluded({3,3,8,8,.25,true}),"equal depth falsely culled");
    check(!p.occluded({3,3,8,8,0,true}),"near plane falsely culled");
    check(!p.occluded({3,3,8,8,.75,false}),"invalid rectangle culled");
    check(!p.occluded({-8,3,-1,8,.75,true}),"offscreen rectangle culled");
    check(!p.occluded({NAN,3,8,8,.75,true}),"NaN rectangle culled");
    auto input=std::vector<float>(221,.25F);input[6*17+6]=1;
    p.build(17,13,input);check(!p.occluded({5,5,7,7,.75,true}),"uncovered cell lost");
    input[6*17+6]=NAN;p.build(17,13,input);check(!p.occluded({5,5,7,7,.75,true}),"invalid depth became occluder");
    Mat4 identity{{1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1}};
    const auto good=project_bounds({{-.5,-.5,.2},{.5,.5,.8}},identity,256,144);
    check(good.valid && good.min_x<=64 && good.min_x>63.99 && good.max_x>=192 && good.max_x<192.01 && good.nearest_depth<=.2 && good.nearest_depth>.199,"projection contract");
    check(!project_bounds({{-.5,-.5,-.2},{.5,.5,.8}},identity,256,144).valid,"near-crossing box accepted");
    identity.row_major[15]=0;
    check(!project_bounds({{-.5,-.5,.2},{.5,.5,.8}},identity,256,144).valid,"zero w accepted");
    Mat4 cancellation{{1,0,0,0,0,1,0,0,0,0,1,0,1e16,0,0,-1e16}};
    check(!project_bounds({{1,0,.2},{1,1,.8}},cancellation,256,144).valid,"ill-conditioned projective denominator accepted");
    ClipTriangle bad{{vertex(-1,-1,.3),vertex(1,-1,.3),{0,1,.3,-1}}};
    p.rasterize(32,18,std::span(&bad,1));
    check(std::all_of(p.levels()[0].depth.begin(),p.levels()[0].depth.end(),[](float z){return z==1;}),"near-crossing triangle wrote depth");
}
void random_scene(std::mt19937_64& rng) {
    const auto w=std::uint32_t(9+rng()%88),h=std::uint32_t(7+rng()%50);
    std::uniform_real_distribution<double> unit(0,1);
    std::vector<float> d(std::size_t(w)*h,.2F);
    for (auto& z:d) {const auto r=rng()%50;if(r==0)z=1;else if(r==1)z=NAN;else if(r==2)z=-1;else if(r==3)z=INFINITY;else if(r==4)z=float(unit(rng));}
    DepthPyramid p;p.build(w,h,d);
    for(unsigned i=0;i<60;++i) {
        const double x=(unit(rng)*1.4-.2)*w,y=(unit(rng)*1.4-.2)*h;
        ScreenRect r{x,y,x+unit(rng)*w*.25+.01,y+unit(rng)*h*.25+.01,unit(rng),true};
        ++queries;check(!p.occluded(r)||scalar(p,r),"hierarchy false occlusion vs full-resolution scalar oracle");
    }
    // Independent barycentric center-depth oracle for random perspective triangles.
    std::vector<ClipTriangle> triangles;
    for(unsigned i=0;i<4;++i) {ClipTriangle t;for(auto& v:t.vertices)v=vertex(unit(rng)*3-1.5,unit(rng)*3-1.5,unit(rng),.1+unit(rng)*9);triangles.push_back(t);}
    p.rasterize(w,h,triangles);
    std::vector<double> centers(std::size_t(w)*h,1);
    for(const auto& t:triangles) {
        double xs[3],ys[3],zs[3];
        for(unsigned i=0;i<3;++i){xs[i]=(t.vertices[i].x/t.vertices[i].w*.5+.5)*w;ys[i]=(.5-t.vertices[i].y/t.vertices[i].w*.5)*h;zs[i]=t.vertices[i].z/t.vertices[i].w;}
        const double det=(ys[1]-ys[2])*(xs[0]-xs[2])+(xs[2]-xs[1])*(ys[0]-ys[2]);
        if(std::abs(det)<1e-12)continue;
        for(std::uint32_t y=0;y<h;++y)for(std::uint32_t x=0;x<w;++x){
            const double px=x+.5,py=y+.5;
            const double a=((ys[1]-ys[2])*(px-xs[2])+(xs[2]-xs[1])*(py-ys[2]))/det;
            const double b=((ys[2]-ys[0])*(px-xs[2])+(xs[0]-xs[2])*(py-ys[2]))/det,c=1-a-b;
            if(a>=0&&b>=0&&c>=0) centers[std::size_t(y)*w+x]=std::min(centers[std::size_t(y)*w+x],a*zs[0]+b*zs[1]+c*zs[2]);
        }
    }
    for(std::size_t i=0;i<centers.size();++i)check(p.levels()[0].depth[i]+1e-8>=centers[i],"raster depth closer than independent full-resolution oracle");
    // Boundary samples also reject center-only coverage accidentally used as full-cell coverage.
    for(std::uint32_t y=0;y<h;++y)for(std::uint32_t x=0;x<w;++x)if(p.levels()[0].depth[std::size_t(y)*w+x]<1)
        for(unsigned corner=0;corner<4;++corner){
            const double px=double(x)+(corner&1),py=double(y)+((corner>>1)&1);double nearest=1;
            for(const auto& t:triangles){double xs[3],ys[3],zs[3];for(unsigned k=0;k<3;++k){xs[k]=(t.vertices[k].x/t.vertices[k].w*.5+.5)*w;ys[k]=(.5-t.vertices[k].y/t.vertices[k].w*.5)*h;zs[k]=t.vertices[k].z/t.vertices[k].w;}
                const double det=(ys[1]-ys[2])*(xs[0]-xs[2])+(xs[2]-xs[1])*(ys[0]-ys[2]);if(std::abs(det)<1e-12)continue;
                const double a=((ys[1]-ys[2])*(px-xs[2])+(xs[2]-xs[1])*(py-ys[2]))/det;
                const double b=((ys[2]-ys[0])*(px-xs[2])+(xs[0]-xs[2])*(py-ys[2]))/det,c=1-a-b;
                if(a>=0&&b>=0&&c>=0)nearest=std::min(nearest,a*zs[0]+b*zs[1]+c*zs[2]);
            }
            check(p.levels()[0].depth[std::size_t(y)*w+x]+1e-8>=nearest,"cell boundary not conservatively covered");
        }
    for(unsigned i=0;i<24;++i){
        const double x=unit(rng)*(w-3),y=unit(rng)*(h-3);
        ScreenRect r{x,y,x+2,y+2,unit(rng),true};++queries;
        if(p.occluded(r))for(std::uint32_t py=0;py<h;++py)for(std::uint32_t px=0;px<w;++px)
            if(px+.5>=r.min_x&&px+.5<=r.max_x&&py+.5>=r.min_y&&py+.5<=r.max_y)
                check(centers[std::size_t(py)*w+px]<r.nearest_depth,"raster/query false occlusion vs original triangle samples");
    }
    ++scenes;
}
double elapsed(std::chrono::steady_clock::time_point t) {return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-t).count();}
void benchmark() {
    using clock=std::chrono::steady_clock;
    const std::vector<ClipTriangle> walls={{{vertex(-1.1,-1.1,.3),vertex(.2,-1.1,.3),vertex(-1.1,1.1,.3)}},{{vertex(.2,-1.1,.3),vertex(.2,1.1,.3),vertex(-1.1,1.1,.3)}}};
    DepthPyramid p;auto start=clock::now();for(unsigned i=0;i<100;++i)p.rasterize(256,144,walls);const double raster=elapsed(start)/100;
    auto base=p.levels()[0].depth;start=clock::now();for(unsigned i=0;i<1000;++i)p.build(256,144,base);const double build=elapsed(start)/1000;
    std::vector<ScreenRect> objects;std::mt19937_64 rng(987654);std::uniform_real_distribution<double> unit(0,1);
    for(unsigned i=0;i<4096;++i){double x=unit(rng)*240,y=unit(rng)*128;objects.push_back({x,y,x+4+unit(rng)*12,y+4+unit(rng)*12,.1+unit(rng)*.8,true});}
    std::uint64_t hidden=0,scalar_hidden=0;start=clock::now();for(unsigned iteration=0;iteration<100;++iteration)for(const auto& r:objects)hidden+=p.occluded(r);const double hiz=elapsed(start)/100;
    start=clock::now();for(const auto& r:objects)scalar_hidden+=scalar(p,r);const double scan=elapsed(start);
    std::cout<<",\"benchmark\":{\"width\":256,\"height\":144,\"objects\":4096,\"raster_and_pyramid_ms\":"<<raster<<",\"pyramid_only_ms\":"<<build<<",\"hiz_queries_ms\":"<<hiz<<",\"scalar_queries_ms\":"<<scan<<",\"hiz_culled\":"<<hidden/100<<",\"scalar_culled\":"<<scalar_hidden<<",\"logical_processors\":"<<std::thread::hardware_concurrency()<<"}";
}
}
int main(int argc,char** argv) {
    try {
        double seconds=1;if(argc==3&&std::string(argv[1])=="--seconds")seconds=std::stod(argv[2]);
        if(!std::isfinite(seconds)||seconds<0||seconds>3600)throw std::runtime_error("invalid seconds");
        fixed_tests();std::mt19937_64 rng(0x578AB109DULL);const auto start=std::chrono::steady_clock::now();
        do{random_scene(rng);}while(elapsed(start)<seconds*1000);
        std::cout<<"{\"status\":\"PASS\",\"seed\":"<<0x578AB109DULL<<",\"property_seconds\":"<<elapsed(start)/1000<<",\"scenes\":"<<scenes<<",\"queries\":"<<queries<<",\"checks\":"<<checks;
        benchmark();std::cout<<"}\n";return 0;
    }catch(const std::exception& e){std::cerr<<"render_host failure: "<<e.what()<<'\n';return 1;}
}
