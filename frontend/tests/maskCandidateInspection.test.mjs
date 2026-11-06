import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

// Exercise the actual editor pixel function without a browser or production build.
const fixturePath=fileURLToPath(new URL('./mask-candidate-fixture.cjs',import.meta.url));
const compiled=await build({stdin:{contents:"export {maskInspectionPixels} from './src/components/SceneMaskEditor.jsx';",resolveDir:fileURLToPath(new URL('..',import.meta.url)),sourcefile:'mask-candidate-fixture.js'},bundle:true,write:false,format:'cjs',platform:'node',logLevel:'silent'});
const fixture=new Module(fixturePath);fixture._compile(compiled.outputFiles[0].text,fixturePath);
const {maskInspectionPixels}=fixture.exports;
const width=10,height=8;
const image=color=>{const pixels=new Uint8ClampedArray(width*height*4);for(let index=0;index<pixels.length;index+=4)pixels.set([...color,255],index);return pixels;};
const fill=(pixels,left,top,right,bottom,color)=>{for(let y=top;y<bottom;y++)for(let x=left;x<right;x++)pixels.set([...color,255],(y*width+x)*4);};
const pixel=(pixels,x,y)=>Array.from(pixels.slice((y*width+x)*4,(y*width+x)*4+3));
test('source mask overlay retains source pixels and protection priority without mixing candidate pixels',()=>{
 const source=image([20,25,30]),edit=image([0,0,0]),protect=image([0,0,0]);
 fill(edit,4,3,7,7,[255,255,255]);
 fill(protect,6,4,7,6,[255,255,255]);
 const before=[source.slice(),edit.slice(),protect.slice()];
 const result=maskInspectionPixels(source,edit,protect,{editColor:[255,116,72],protectColor:[65,171,255]});
 assert.deepEqual(pixel(result,5,4),[119,63,48]);
 assert.deepEqual(pixel(result,6,4),[43,98,143]);
 assert.deepEqual(pixel(result,0,0),pixel(source,0,0));
 assert.deepEqual([source,edit,protect],before);
});

test('mask inspection uses the supplied theme palette without changing source or mask pixels',()=>{
 const source=image([20,25,30]),edit=image([0,0,0]),protect=image([0,0,0]);
 fill(edit,4,3,7,7,[255,255,255]);
 fill(protect,6,4,7,6,[255,255,255]);
 const before=[source.slice(),edit.slice(),protect.slice()];
 const result=maskInspectionPixels(source,edit,protect,{editColor:[10,20,30],protectColor:[200,100,50]});
 assert.deepEqual(pixel(result,5,4),[16,23,30]);
 assert.deepEqual(pixel(result,6,4),[110,63,40]);
 assert.deepEqual(pixel(result,0,0),pixel(source,0,0));
 assert.deepEqual([source,edit,protect],before);
});
