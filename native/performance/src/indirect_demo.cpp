// Standalone offscreen D3D12 experiment. No HWND, swap chain, injection or game hooks.
#include "occlusion.hpp"
#include "shader_sources.hpp"
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;
using namespace riftstone::perf;
namespace {
constexpr UINT width=256,height=144;
struct Object { float left,top,right,bottom,depth;UINT id,valid,padding; };
struct Command { UINT object;D3D12_DRAW_ARGUMENTS draw; };
static_assert(sizeof(Object)==32 && sizeof(Command)==20);
struct Unavailable:std::runtime_error {using runtime_error::runtime_error;};
void require(bool okay,const char* message) {if(!okay)throw std::runtime_error(message);}
void hr(HRESULT result,const char* operation) {
    if(FAILED(result))throw std::runtime_error(std::string(operation)+" HRESULT="+std::to_string(static_cast<unsigned long>(result)));
}
std::string json(const std::string& input) {
    std::string out="\"";
    for(unsigned char c:input){if(c=='"'||c=='\\'){out+='\\';out+=char(c);}else if(c<32)out+=' ';else out+=char(c);}
    return out+'"';
}
std::string utf8(const wchar_t* s) {
    const int count=WideCharToMultiByte(CP_UTF8,0,s,-1,nullptr,0,nullptr,nullptr);
    if(count<1)return "unknown";
    std::string out(static_cast<std::size_t>(count),'\0');
    WideCharToMultiByte(CP_UTF8,0,s,-1,out.data(),count,nullptr,nullptr);out.pop_back();return out;
}
ComPtr<ID3DBlob> compile(const char* source,const char* entry,const char* target) {
    ComPtr<ID3DBlob> code,error;
    const HRESULT result=D3DCompile(source,std::strlen(source),"embedded-render-shader",nullptr,nullptr,entry,target,
        D3DCOMPILE_ENABLE_STRICTNESS|D3DCOMPILE_IEEE_STRICTNESS|D3DCOMPILE_WARNINGS_ARE_ERRORS|D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&code,&error);
    if(FAILED(result))throw std::runtime_error(error?std::string(static_cast<const char*>(error->GetBufferPointer()),error->GetBufferSize()):"shader compile failed");
    return code;
}
class Renderer {
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Fence> fence;
    ComPtr<ID3D12RootSignature> compute_root,draw_root;
    ComPtr<ID3D12PipelineState> compute_pipeline,draw_pipeline;
    ComPtr<ID3D12CommandSignature> signature;
    ComPtr<ID3D12InfoQueue> info;
    HANDLE event{};
    UINT64 fence_value{},frequency{};
    bool submitted=false;
    ComPtr<ID3D12Resource> buffer(UINT64 bytes,D3D12_HEAP_TYPE heap,D3D12_RESOURCE_STATES state,D3D12_RESOURCE_FLAGS flags=D3D12_RESOURCE_FLAG_NONE) {
        D3D12_HEAP_PROPERTIES properties{};properties.Type=heap;
        D3D12_RESOURCE_DESC desc{};desc.Dimension=D3D12_RESOURCE_DIMENSION_BUFFER;desc.Width=std::max<UINT64>(bytes,4);
        desc.Height=1;desc.DepthOrArraySize=1;desc.MipLevels=1;desc.SampleDesc.Count=1;desc.Layout=D3D12_TEXTURE_LAYOUT_ROW_MAJOR;desc.Flags=flags;
        ComPtr<ID3D12Resource> resource;
        hr(device->CreateCommittedResource(&properties,D3D12_HEAP_FLAG_NONE,&desc,state,nullptr,IID_PPV_ARGS(&resource)),"create buffer");return resource;
    }
    ComPtr<ID3D12Resource> upload(const void* source,UINT64 bytes) {
        auto resource=buffer(bytes,D3D12_HEAP_TYPE_UPLOAD,D3D12_RESOURCE_STATE_GENERIC_READ);void* mapped=nullptr;
        const D3D12_RANGE empty{0,0};hr(resource->Map(0,&empty,&mapped),"map upload");
        if(bytes)std::memcpy(mapped,source,static_cast<std::size_t>(bytes));resource->Unmap(0,nullptr);return resource;
    }
    void transition(ID3D12Resource* resource,D3D12_RESOURCE_STATES before,D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER barrier{};barrier.Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        barrier.Transition={resource,D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,before,after};list->ResourceBarrier(1,&barrier);
    }
    void root(std::span<const D3D12_ROOT_PARAMETER> params,D3D12_ROOT_SIGNATURE_FLAGS flags,ComPtr<ID3D12RootSignature>& result) {
        D3D12_ROOT_SIGNATURE_DESC desc{};desc.NumParameters=static_cast<UINT>(params.size());desc.pParameters=params.data();desc.Flags=flags;
        ComPtr<ID3DBlob> data,error;hr(D3D12SerializeRootSignature(&desc,D3D_ROOT_SIGNATURE_VERSION_1,&data,&error),"serialize root signature");
        hr(device->CreateRootSignature(0,data->GetBufferPointer(),data->GetBufferSize(),IID_PPV_ARGS(&result)),"create root signature");
    }
    void synchronize() {
        hr(queue->Signal(fence.Get(),++fence_value),"signal GPU fence");
        if(fence->GetCompletedValue()<fence_value) {
            hr(fence->SetEventOnCompletion(fence_value,event),"set GPU event");
            if(WaitForSingleObject(event,30000)!=WAIT_OBJECT_0) {
                // Resources in this stack may still be in flight. Fail-stop avoids freeing them.
                std::cerr<<"GPU fence timeout; terminating owned offscreen host\n";
                TerminateProcess(GetCurrentProcess(),2);std::terminate();
            }
        }
        require(fence->GetCompletedValue()!=UINT64_MAX,"GPU device removed");submitted=false;
    }
public:
    DXGI_ADAPTER_DESC1 adapter_desc{};
    bool debug_layer=false;
    std::vector<double> gpu_ms,cpu_ms;
    std::uint64_t case_count{},object_count{},draw_count{},pixel_checks{},argument_checks{};
    explicit Renderer(bool warp) {
        ComPtr<ID3D12Debug> debug;
        if(SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&debug)))) {debug->EnableDebugLayer();debug_layer=true;}
        ComPtr<IDXGIFactory4> factory;hr(CreateDXGIFactory2(0,IID_PPV_ARGS(&factory)),"create DXGI factory");
        ComPtr<IDXGIAdapter1> selected;
        if(warp) {
            hr(factory->EnumWarpAdapter(IID_PPV_ARGS(&selected)),"enumerate requested WARP");
            if(FAILED(D3D12CreateDevice(selected.Get(),D3D_FEATURE_LEVEL_11_0,IID_PPV_ARGS(&device))))throw Unavailable("requested WARP D3D12 unavailable");
        }else {
            for(UINT index=0;;++index) {
                ComPtr<IDXGIAdapter1> candidate;
                const HRESULT result=factory->EnumAdapters1(index,&candidate);if(result==DXGI_ERROR_NOT_FOUND)break;hr(result,"enumerate adapter");
                DXGI_ADAPTER_DESC1 desc{};hr(candidate->GetDesc1(&desc),"describe adapter");if(desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)continue;
                if(SUCCEEDED(D3D12CreateDevice(candidate.Get(),D3D_FEATURE_LEVEL_11_0,IID_PPV_ARGS(&device)))){selected=candidate;break;}
            }
            if(!selected)throw Unavailable("no hardware D3D12 adapter; --warp is an explicit alternative");
        }
        hr(selected->GetDesc1(&adapter_desc),"selected adapter description");
        if(debug_layer)device.As(&info);
        D3D12_COMMAND_QUEUE_DESC q{};q.Type=D3D12_COMMAND_LIST_TYPE_DIRECT;
        hr(device->CreateCommandQueue(&q,IID_PPV_ARGS(&queue)),"create direct queue");
        hr(queue->GetTimestampFrequency(&frequency),"timestamp frequency");
        hr(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT,IID_PPV_ARGS(&allocator)),"create allocator");
        hr(device->CreateCommandList(0,D3D12_COMMAND_LIST_TYPE_DIRECT,allocator.Get(),nullptr,IID_PPV_ARGS(&list)),"create list");
        hr(list->Close(),"initial close");
        hr(device->CreateFence(0,D3D12_FENCE_FLAG_NONE,IID_PPV_ARGS(&fence)),"create fence");
        std::array<D3D12_ROOT_PARAMETER,5> cp{};
        cp[0].ParameterType=D3D12_ROOT_PARAMETER_TYPE_SRV;cp[0].Descriptor.ShaderRegister=0;
        cp[1].ParameterType=D3D12_ROOT_PARAMETER_TYPE_SRV;cp[1].Descriptor.ShaderRegister=1;
        cp[2].ParameterType=D3D12_ROOT_PARAMETER_TYPE_UAV;cp[2].Descriptor.ShaderRegister=0;
        cp[3].ParameterType=D3D12_ROOT_PARAMETER_TYPE_UAV;cp[3].Descriptor.ShaderRegister=1;
        cp[4].ParameterType=D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;cp[4].Constants={0,0,4};root(cp,D3D12_ROOT_SIGNATURE_FLAG_NONE,compute_root);
        std::array<D3D12_ROOT_PARAMETER,3> gp{};
        gp[0].ParameterType=D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;gp[0].Constants={0,0,1};
        gp[1].ParameterType=D3D12_ROOT_PARAMETER_TYPE_SRV;gp[1].Descriptor.ShaderRegister=0;
        gp[2].ParameterType=D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;gp[2].Constants={1,0,2};root(gp,D3D12_ROOT_SIGNATURE_FLAG_NONE,draw_root);
        const auto cs=compile(cull_source,"main","cs_5_1");D3D12_COMPUTE_PIPELINE_STATE_DESC compute{};compute.pRootSignature=compute_root.Get();compute.CS={cs->GetBufferPointer(),cs->GetBufferSize()};
        hr(device->CreateComputePipelineState(&compute,IID_PPV_ARGS(&compute_pipeline)),"create compute pipeline");
        const auto vs=compile(draw_source,"vertex","vs_5_1"),ps=compile(draw_source,"pixel","ps_5_1");
        D3D12_GRAPHICS_PIPELINE_STATE_DESC graphics{};graphics.pRootSignature=draw_root.Get();graphics.VS={vs->GetBufferPointer(),vs->GetBufferSize()};graphics.PS={ps->GetBufferPointer(),ps->GetBufferSize()};
        auto& blend=graphics.BlendState.RenderTarget[0];blend.SrcBlend=D3D12_BLEND_ONE;blend.DestBlend=D3D12_BLEND_ZERO;blend.BlendOp=D3D12_BLEND_OP_ADD;
        blend.SrcBlendAlpha=D3D12_BLEND_ONE;blend.DestBlendAlpha=D3D12_BLEND_ZERO;blend.BlendOpAlpha=D3D12_BLEND_OP_ADD;blend.LogicOp=D3D12_LOGIC_OP_NOOP;
        graphics.BlendState.RenderTarget[0].RenderTargetWriteMask=D3D12_COLOR_WRITE_ENABLE_ALL;
        graphics.RasterizerState.FillMode=D3D12_FILL_MODE_SOLID;graphics.RasterizerState.CullMode=D3D12_CULL_MODE_NONE;graphics.RasterizerState.DepthClipEnable=TRUE;
        graphics.DepthStencilState.DepthEnable=FALSE;graphics.DepthStencilState.DepthFunc=D3D12_COMPARISON_FUNC_ALWAYS;graphics.DepthStencilState.StencilEnable=FALSE;
        graphics.SampleMask=UINT_MAX;graphics.PrimitiveTopologyType=D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;graphics.NumRenderTargets=1;graphics.RTVFormats[0]=DXGI_FORMAT_R8G8B8A8_UNORM;graphics.SampleDesc.Count=1;
        hr(device->CreateGraphicsPipelineState(&graphics,IID_PPV_ARGS(&draw_pipeline)),"create graphics pipeline");
        std::array<D3D12_INDIRECT_ARGUMENT_DESC,2> arguments{};
        arguments[0].Type=D3D12_INDIRECT_ARGUMENT_TYPE_CONSTANT;arguments[0].Constant={0,0,1};arguments[1].Type=D3D12_INDIRECT_ARGUMENT_TYPE_DRAW;
        D3D12_COMMAND_SIGNATURE_DESC command{};command.ByteStride=sizeof(Command);command.NumArgumentDescs=2;command.pArgumentDescs=arguments.data();
        hr(device->CreateCommandSignature(&command,draw_root.Get(),IID_PPV_ARGS(&signature)),"create indirect signature");
        event=CreateEventW(nullptr,FALSE,FALSE,nullptr);require(event!=nullptr,"create fence event");
    }
    ~Renderer() {
        if(submitted) {try{synchronize();}catch(...){TerminateProcess(GetCurrentProcess(),2);}}
        if(event)CloseHandle(event);
    }
    void run(const std::vector<Object>& objects,const DepthPyramid& pyramid) {
        require(objects.size()<=16384,"object capacity exceeded");require(!pyramid.levels().empty(),"missing depth");
        require(pyramid.levels()[0].width==width&&pyramid.levels()[0].height==height,"depth dimensions differ from offscreen view");
        std::vector<UINT> expected;
        std::vector<float> depth;
        for(const auto& level:pyramid.levels())depth.insert(depth.end(),level.depth.begin(),level.depth.end());
        for(UINT i=0;i<objects.size();++i) {
            const auto& o=objects[i];if(!pyramid.occluded({o.left,o.top,o.right,o.bottom,o.depth,o.valid!=0}))expected.push_back(i);
        }
        auto object_buffer=upload(objects.data(),objects.size()*sizeof(Object));auto depth_buffer=upload(depth.data(),depth.size()*sizeof(float));
        const UINT capacity=std::max<UINT>(static_cast<UINT>(objects.size()),1),zero=0;
        auto zero_buffer=upload(&zero,sizeof(zero));
        auto argument_buffer=buffer(UINT64(capacity)*sizeof(Command),D3D12_HEAP_TYPE_DEFAULT,D3D12_RESOURCE_STATE_UNORDERED_ACCESS,D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS);
        auto count_buffer=buffer(4,D3D12_HEAP_TYPE_DEFAULT,D3D12_RESOURCE_STATE_COPY_DEST,D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS);
        auto argument_readback=buffer(UINT64(capacity)*sizeof(Command),D3D12_HEAP_TYPE_READBACK,D3D12_RESOURCE_STATE_COPY_DEST);
        auto count_readback=buffer(4,D3D12_HEAP_TYPE_READBACK,D3D12_RESOURCE_STATE_COPY_DEST);
        auto timestamp_readback=buffer(16,D3D12_HEAP_TYPE_READBACK,D3D12_RESOURCE_STATE_COPY_DEST);
        D3D12_RESOURCE_DESC image{};image.Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D;image.Width=width;image.Height=height;image.DepthOrArraySize=1;image.MipLevels=1;image.Format=DXGI_FORMAT_R8G8B8A8_UNORM;image.SampleDesc.Count=1;image.Flags=D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
        D3D12_HEAP_PROPERTIES properties{};properties.Type=D3D12_HEAP_TYPE_DEFAULT;
        D3D12_CLEAR_VALUE clear{};clear.Format=image.Format;clear.Color[3]=1;
        ComPtr<ID3D12Resource> target;hr(device->CreateCommittedResource(&properties,D3D12_HEAP_FLAG_NONE,&image,D3D12_RESOURCE_STATE_RENDER_TARGET,&clear,IID_PPV_ARGS(&target)),"create offscreen target");
        D3D12_DESCRIPTOR_HEAP_DESC heap{};heap.Type=D3D12_DESCRIPTOR_HEAP_TYPE_RTV;heap.NumDescriptors=1;
        ComPtr<ID3D12DescriptorHeap> rtv;hr(device->CreateDescriptorHeap(&heap,IID_PPV_ARGS(&rtv)),"create RTV heap");
        const auto handle=rtv->GetCPUDescriptorHandleForHeapStart();device->CreateRenderTargetView(target.Get(),nullptr,handle);
        D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};UINT64 image_bytes{};device->GetCopyableFootprints(&image,0,1,0,&footprint,nullptr,nullptr,&image_bytes);
        auto image_readback=buffer(image_bytes,D3D12_HEAP_TYPE_READBACK,D3D12_RESOURCE_STATE_COPY_DEST);
        D3D12_QUERY_HEAP_DESC query_desc{};query_desc.Type=D3D12_QUERY_HEAP_TYPE_TIMESTAMP;query_desc.Count=2;
        ComPtr<ID3D12QueryHeap> query;hr(device->CreateQueryHeap(&query_desc,IID_PPV_ARGS(&query)),"create timestamp query");
        hr(allocator->Reset(),"reset allocator");hr(list->Reset(allocator.Get(),nullptr),"reset list");
        list->CopyBufferRegion(count_buffer.Get(),0,zero_buffer.Get(),0,4);
        transition(count_buffer.Get(),D3D12_RESOURCE_STATE_COPY_DEST,D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        list->EndQuery(query.Get(),D3D12_QUERY_TYPE_TIMESTAMP,0);
        list->SetPipelineState(compute_pipeline.Get());list->SetComputeRootSignature(compute_root.Get());
        list->SetComputeRootShaderResourceView(0,object_buffer->GetGPUVirtualAddress());list->SetComputeRootShaderResourceView(1,depth_buffer->GetGPUVirtualAddress());
        list->SetComputeRootUnorderedAccessView(2,argument_buffer->GetGPUVirtualAddress());list->SetComputeRootUnorderedAccessView(3,count_buffer->GetGPUVirtualAddress());
        const UINT params[4]={width,height,static_cast<UINT>(objects.size()),static_cast<UINT>(pyramid.levels().size())};list->SetComputeRoot32BitConstants(4,4,params,0);
        if(!objects.empty())list->Dispatch((static_cast<UINT>(objects.size())+63)/64,1,1);
        D3D12_RESOURCE_BARRIER barriers[2]{};for(auto& b:barriers)b.Type=D3D12_RESOURCE_BARRIER_TYPE_UAV;
        barriers[0].UAV.pResource=argument_buffer.Get();barriers[1].UAV.pResource=count_buffer.Get();list->ResourceBarrier(2,barriers);
        transition(argument_buffer.Get(),D3D12_RESOURCE_STATE_UNORDERED_ACCESS,D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT);
        transition(count_buffer.Get(),D3D12_RESOURCE_STATE_UNORDERED_ACCESS,D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT);
        list->SetPipelineState(draw_pipeline.Get());list->SetGraphicsRootSignature(draw_root.Get());
        list->SetGraphicsRoot32BitConstant(0,0,0);list->SetGraphicsRootShaderResourceView(1,object_buffer->GetGPUVirtualAddress());
        const float view[2]={float(width),float(height)};list->SetGraphicsRoot32BitConstants(2,2,view,0);
        D3D12_VIEWPORT viewport{0,0,float(width),float(height),0,1};D3D12_RECT scissor{0,0,LONG(width),LONG(height)};
        list->RSSetViewports(1,&viewport);list->RSSetScissorRects(1,&scissor);list->OMSetRenderTargets(1,&handle,FALSE,nullptr);
        list->ClearRenderTargetView(handle,clear.Color,0,nullptr);list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        list->ExecuteIndirect(signature.Get(),capacity,argument_buffer.Get(),0,count_buffer.Get(),0);
        list->EndQuery(query.Get(),D3D12_QUERY_TYPE_TIMESTAMP,1);list->ResolveQueryData(query.Get(),D3D12_QUERY_TYPE_TIMESTAMP,0,2,timestamp_readback.Get(),0);
        transition(argument_buffer.Get(),D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT,D3D12_RESOURCE_STATE_COPY_SOURCE);
        transition(count_buffer.Get(),D3D12_RESOURCE_STATE_INDIRECT_ARGUMENT,D3D12_RESOURCE_STATE_COPY_SOURCE);
        transition(target.Get(),D3D12_RESOURCE_STATE_RENDER_TARGET,D3D12_RESOURCE_STATE_COPY_SOURCE);
        list->CopyBufferRegion(argument_readback.Get(),0,argument_buffer.Get(),0,UINT64(capacity)*sizeof(Command));list->CopyBufferRegion(count_readback.Get(),0,count_buffer.Get(),0,4);
        D3D12_TEXTURE_COPY_LOCATION source{};source.pResource=target.Get();source.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        D3D12_TEXTURE_COPY_LOCATION destination{};destination.pResource=image_readback.Get();destination.Type=D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;destination.PlacedFootprint=footprint;
        list->CopyTextureRegion(&destination,0,0,0,&source,nullptr);hr(list->Close(),"close commands");
        const auto began=std::chrono::steady_clock::now();ID3D12CommandList* commands[]={list.Get()};submitted=true;queue->ExecuteCommandLists(1,commands);
        try{synchronize();}catch(...){TerminateProcess(GetCurrentProcess(),2);std::terminate();}
        cpu_ms.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-began).count());
        const D3D12_RANGE no_write{0,0};void* mapped=nullptr;D3D12_RANGE range{0,4};hr(count_readback->Map(0,&range,&mapped),"read draw count");
        UINT actual{};std::memcpy(&actual,mapped,4);count_readback->Unmap(0,&no_write);require(actual==expected.size(),"GPU visibility count differs from CPU oracle");
        range={0,std::size_t(actual)*sizeof(Command)};hr(argument_readback->Map(0,&range,&mapped),"read draw arguments");
        std::vector<UINT> ids;const auto* records=static_cast<const Command*>(mapped);
        for(UINT i=0;i<actual;++i){const auto& c=records[i];require(c.object<objects.size(),"GPU draw index outside objects");require(c.draw.VertexCountPerInstance==6&&c.draw.InstanceCount==1&&c.draw.StartVertexLocation==0&&c.draw.StartInstanceLocation==0,"GPU malformed draw record");ids.push_back(c.object);++argument_checks;}
        argument_readback->Unmap(0,&no_write);std::sort(ids.begin(),ids.end());require(ids==expected,"GPU visible IDs differ from CPU oracle");
        std::vector<bool> white(std::size_t(width)*height,false);
        for(UINT index:expected){const auto& o=objects[index];if(!std::isfinite(o.left)||!std::isfinite(o.top)||!std::isfinite(o.right)||!std::isfinite(o.bottom)||!std::isfinite(o.depth)||o.left>=o.right||o.top>=o.bottom)continue;
            for(UINT y=0;y<height;++y)for(UINT x=0;x<width;++x)if(x+.5>=o.left&&x+.5<o.right&&y+.5>=o.top&&y+.5<o.bottom)white[std::size_t(y)*width+x]=true;
        }
        range={0,static_cast<SIZE_T>(image_bytes)};hr(image_readback->Map(0,&range,&mapped),"read rendered pixels");
        const auto* pixels=static_cast<const unsigned char*>(mapped)+footprint.Offset;
        bool pixels_match=true;
        for(UINT y=0;y<height;++y)for(UINT x=0;x<width;++x){const auto* pixel=pixels+std::size_t(y)*footprint.Footprint.RowPitch+x*4;const unsigned char wanted=white[std::size_t(y)*width+x]?255:0;
            pixels_match=pixels_match&&pixel[0]==wanted&&pixel[1]==wanted&&pixel[2]==wanted&&pixel[3]==255;++pixel_checks;}
        image_readback->Unmap(0,&no_write);require(pixels_match,"ExecuteIndirect image differs from scalar rectangle coverage");
        range={0,16};hr(timestamp_readback->Map(0,&range,&mapped),"read timestamps");UINT64 timestamps[2]{};std::memcpy(timestamps,mapped,16);timestamp_readback->Unmap(0,&no_write);
        require(timestamps[1]>=timestamps[0]&&frequency>0,"invalid GPU timestamp");gpu_ms.push_back(double(timestamps[1]-timestamps[0])*1000/double(frequency));
        ++case_count;object_count+=objects.size();draw_count+=actual;
    }
    UINT64 debug_errors() {
        if(!info)return 0;
        UINT64 errors=0;
        for(UINT64 i=0;i<info->GetNumStoredMessages();++i){SIZE_T bytes=0;hr(info->GetMessage(i,nullptr,&bytes),"size debug message");std::vector<unsigned char> data(bytes);auto* m=reinterpret_cast<D3D12_MESSAGE*>(data.data());hr(info->GetMessage(i,m,&bytes),"read debug message");
            if(m->Severity==D3D12_MESSAGE_SEVERITY_CORRUPTION||m->Severity==D3D12_MESSAGE_SEVERITY_ERROR){++errors;std::cerr<<"D3D12: "<<m->pDescription<<'\n';}}
        return errors;
    }
};
void timing(const char* name,const std::vector<double>& values) {
    double total=0;for(double value:values)total+=value;
    std::cout<<",\""<<name<<"\":{\"min\":"<<*std::min_element(values.begin(),values.end())<<",\"mean\":"<<total/double(values.size())<<",\"max\":"<<*std::max_element(values.begin(),values.end())<<"}";
}
void scenes(Renderer& renderer) {
    std::mt19937 rng(728491);
    for(unsigned scene=0;scene<20;++scene){
        DepthPyramid p;std::vector<float> depth(std::size_t(width)*height,scene==1?1.F:.25F);
        if(scene>=3)for(UINT y=0;y<height;++y)for(UINT x=0;x<width;++x)if(x>120||(x>40&&x<60&&y>40&&y<80))depth[std::size_t(y)*width+x]=1;
        p.build(width,height,depth);
        if(scene==4){std::vector<ClipTriangle> wall={{{ClipVertex{-1.1,-1.1,.25,1},ClipVertex{.1,-1.1,.25,1},ClipVertex{-1.1,1.1,.25,1}}},{{ClipVertex{.1,-1.1,.25,1},ClipVertex{.1,1.1,.25,1},ClipVertex{-1.1,1.1,.25,1}}}};p.rasterize(width,height,wall);}
        std::vector<Object> objects;
        const unsigned count=scene==0?0:(scene==1?129:(scene==2?257:1024));
        for(unsigned i=0;i<count;++i){const float x=float(2+rng()%238),y=float(2+rng()%126);objects.push_back({x,y,x+float(2+rng()%14),y+float(2+rng()%14),scene==2?.8F:((i%3==0)?.1F:.8F),i,1,0});}
        if(scene>=5){objects.push_back({10,10,20,20,0,2000,1,0});objects.push_back({20,20,30,30,.8F,2001,0,0});objects.push_back({-20,-20,-2,-2,.8F,2002,1,0});objects.push_back({30,30,40,40,std::numeric_limits<float>::quiet_NaN(),2003,1,0});objects.push_back({NAN,0,1,1,.8F,2004,1,0});objects.push_back({50,50,40,40,.8F,2005,1,0});}
        renderer.run(objects,p);
    }
}
}
int main(int argc,char** argv) {
    try {
        bool warp=false;if(argc==2&&std::string(argv[1])=="--warp")warp=true;else if(argc!=1)throw std::runtime_error("usage: indirect_demo [--warp]");
        Renderer renderer(warp);scenes(renderer);const UINT64 errors=renderer.debug_errors();require(errors==0,"D3D12 debug layer reported errors");
        std::cout<<"{\"status\":\"PASS\",\"adapter\":"<<json(utf8(renderer.adapter_desc.Description))<<",\"warp_requested\":"<<(warp?"true":"false")<<",\"software_adapter\":"<<((renderer.adapter_desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)?"true":"false")<<",\"vendor_id\":"<<renderer.adapter_desc.VendorId<<",\"device_id\":"<<renderer.adapter_desc.DeviceId<<",\"debug_layer\":"<<(renderer.debug_layer?"true":"false")<<",\"debug_errors\":"<<(renderer.debug_layer?std::to_string(errors):"null")<<",\"width\":256,\"height\":144,\"cases\":"<<renderer.case_count<<",\"objects\":"<<renderer.object_count<<",\"draws\":"<<renderer.draw_count<<",\"argument_checks\":"<<renderer.argument_checks<<",\"pixel_checks\":"<<renderer.pixel_checks;
        timing("gpu_compute_and_draw_ms",renderer.gpu_ms);timing("cpu_submit_and_wait_ms",renderer.cpu_ms);std::cout<<"}\n";return 0;
    }catch(const Unavailable& e){std::cout<<"{\"status\":\"UNAVAILABLE\",\"reason\":"<<json(e.what())<<"}\n";return 3;}
    catch(const std::exception& e){std::cout<<"{\"status\":\"FAIL\",\"reason\":"<<json(e.what())<<"}\n";return 1;}
}
