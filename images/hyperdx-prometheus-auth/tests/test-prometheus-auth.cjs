'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), http=require('node:http'), path=require('node:path');
const {createRequire}=require('node:module');
const route=process.env.HYPERDX_ROUTE_FILE || '/app/packages/api/build/routers/api/prometheus.js';
const real=createRequire(process.env.TEST_MODULE_ROOT || route), express=real('express'), {z}=real('zod');
let connection, hits=[], selected=[], chFallback=false, logs=[];
const counters={add(){},record(){}};
const mocks={
 '../../controllers/connection':{getConnectionById:async(team,id,password)=>{selected.push(password);return team==='owned'&&id==='012345678901234567890123'?{...connection,password:password?connection.password:undefined}:null;}},
 '../../middleware/auth':{getNonNullUserWithTeam:req=>({teamId:req.headers['x-test-team']||'owned'})},
 '../../utils/instrumentation':{getCounter:()=>counters,getHistogram:()=>counters},
 '../../utils/logger':{error:(...args)=>logs.push(args)},
 '../../utils/zod':{objectIdSchema:z.string().regex(/^[0-9a-f]{24}$/),stringListQueryParam:z.union([z.string(),z.array(z.string())]).transform(x=>Array.isArray(x)?x:[x])},
 '../../clickhouse':{ClickhouseClient:class{query(){return {json:async()=>[{version:'26.8'}]};}}},
 '../../controllers/timeseriesEngine':{connectionSupportsPrometheusHttpApi:async()=>!chFallback,PROMETHEUS_MAX_EXECUTION_SEC:30,PROMETHEUS_MAX_RESULT_ROWS:1000,queryInstantViaTableFunction:async()=>[],queryRangeViaTableFunction:async()=>[],queryLabelNames:async()=>['job'],queryLabelValues:async()=>['test']}
};
const mod={exports:{}};
vm.compileFunction(fs.readFileSync(route,'utf8'),['exports','require','module','__filename','__dirname'],{filename:route})(mod.exports,n=>mocks[n]||real(n),mod,route,path.dirname(route));
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve('http://127.0.0.1:'+server.address().port)));
const close=server=>new Promise(resolve=>server.close(resolve));
let api,upstream,apiUrl,upstreamUrl;
test.before(async()=>{
 upstream=http.createServer((req,res)=>{
  hits.push({path:req.url,auth:req.headers.authorization,ch:req.headers['x-clickhouse-user']});
  const expected='Basic '+Buffer.from('test-user:test-password').toString('base64');
  if(req.headers.authorization!==expected && !req.headers['x-clickhouse-user']){res.writeHead(401);res.end('{"status":"error"}');return;}
  res.setHeader('Content-Type','application/json');res.end('{"status":"success","data":{"resultType":"vector","result":[]}}');
 });upstreamUrl=await listen(upstream);
 const app=express();app.use(express.json());app.use('/v1/prometheus',mod.exports.default);api=http.createServer(app);apiUrl=await listen(api);
});
test.after(async()=>{await close(api);await close(upstream);});
async function request(endpoint,method='GET',team='owned',extra={}){
 const q={connectionId:'012345678901234567890123',query:'vector(1)',start:'1700000000',end:'1700000030',step:'15',...extra};
 const url=apiUrl+'/v1/prometheus'+endpoint+(method==='GET'?'?'+new URLSearchParams(q):'');
 const r=await fetch(url,{method,headers:{'Content-Type':'application/json','x-test-team':team},...(method==='POST'?{body:JSON.stringify(q)}:{})});return {status:r.status,body:await r.text()};
}
test.beforeEach(()=>{connection={id:'012345678901234567890123',host:upstreamUrl,username:'test-user',password:'test-password',isPrometheusEndpoint:true};hits=[];selected=[];logs=[];chFallback=false;});
for(const endpoint of ['/query','/query_range','/query_exemplars','/labels','/label/__name__/values']) for(const method of (endpoint.includes('query')?['GET','POST']:['GET'])){
 test(method+' '+endpoint+' forwards stored BasicAuth',async()=>{const r=await request(endpoint,method);assert.equal(r.status,200);assert.equal(hits.length,1);assert.equal(hits[0].auth,'Basic '+Buffer.from('test-user:test-password').toString('base64'));assert.equal(selected[0],true);});
}
test('wrong password remains 401 and is not reflected',async()=>{connection.password='incorrect';const r=await request('/query');assert.equal(r.status,401);assert.ok(!r.body.includes('incorrect'));});
test('no credentials remain unauthenticated',async()=>{delete connection.username;delete connection.password;const r=await request('/query');assert.equal(r.status,401);assert.equal(hits[0].auth,undefined);});
test('foreign team cannot query connection or upstream',async()=>{const r=await request('/query','GET','foreign');assert.equal(r.status,404);assert.equal(hits.length,0);});
test('request params cannot replace stored credentials',async()=>{const r=await request('/query','POST','owned',{Authorization:'attacker',username:'attacker',password:'attacker'});assert.equal(r.status,200);assert.ok(!hits[0].path.includes('attacker'));});
for(const [username,password] of [['user',''],['','pass'],['유저','암호:값'],['','']]) test('credential boundary '+JSON.stringify([username,Boolean(password)]),async()=>{
 connection.username=username;connection.password=password;await request('/query');assert.equal(hits[0].auth,username||password?'Basic '+Buffer.from(username+':'+password).toString('base64'):undefined);
});
test('ClickHouse fallback remains independent of Prometheus BasicAuth',async()=>{connection.isPrometheusEndpoint=false;chFallback=true;const r=await request('/query','GET','owned',{table:'metrics'});assert.equal(r.status,200);assert.equal(hits.length,0);});
