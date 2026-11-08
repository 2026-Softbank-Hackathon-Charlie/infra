package main

import (
 "bytes"
 "crypto/hmac"
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "fmt"
 "io"
 "net/http"
 "net/url"
 "os"
 "strings"
 "time"
)

// Replaced in the test-only source branch so every build has a new layer.
const nonce = "gce-ready-9946e8712e"
func hash(b []byte) string {s:=sha256.Sum256(b);return hex.EncodeToString(s[:])}
func mac(key []byte,value string) []byte {h:=hmac.New(sha256.New,key);h.Write([]byte(value));return h.Sum(nil)}
func s3(method,bucket,key string,body []byte)(int,[]byte,error) {
 u,err:=url.Parse(os.Getenv("S3_ENDPOINT"));if err!=nil{return 0,nil,err};u.Path="/"+bucket;if key!="" {u.Path+="/"+key}
 now:=time.Now().UTC();stamp:=now.Format("20060102T150405Z");day:=stamp[:8];region:=os.Getenv("S3_REGION");scope:=day+"/"+region+"/s3/aws4_request";payload:=hash(body)
 headers:="host:"+u.Host+"\nx-amz-content-sha256:"+payload+"\nx-amz-date:"+stamp+"\n";signed:="host;x-amz-content-sha256;x-amz-date";canonical:=method+"\n"+u.EscapedPath()+"\n\n"+headers+"\n"+signed+"\n"+payload
 signing:=mac(mac(mac(mac([]byte("AWS4"+os.Getenv("S3_SECRET_KEY")),day),region),"s3"),"aws4_request");signature:=hex.EncodeToString(mac(signing,"AWS4-HMAC-SHA256\n"+stamp+"\n"+scope+"\n"+hash([]byte(canonical))))
 req,err:=http.NewRequest(method,u.String(),bytes.NewReader(body));if err!=nil{return 0,nil,err};req.Header.Set("X-Amz-Date",stamp);req.Header.Set("X-Amz-Content-Sha256",payload);req.Header.Set("Authorization","AWS4-HMAC-SHA256 Credential="+os.Getenv("S3_ACCESS_KEY")+"/"+scope+", SignedHeaders="+signed+", Signature="+signature)
 response,err:=(&http.Client{Timeout:8*time.Second}).Do(req);if err!=nil{return 0,nil,err};defer response.Body.Close();data,err:=io.ReadAll(io.LimitReader(response.Body,8192));return response.StatusCode,data,err
}
func main(){
 http.HandleFunc("/healthz",func(w http.ResponseWriter,r *http.Request){fmt.Fprintln(w,"gce-readiness-ok "+nonce)})
 http.HandleFunc("/proof",func(w http.ResponseWriter,r *http.Request){json.NewEncoder(w).Encode(map[string]any{"nonce":nonce,"secretHash":hash([]byte(os.Getenv("READINESS_SECRET"))),"dbConfigured":os.Getenv("PGHOST")!="","nonroot":os.Getuid()!=0})})
 http.HandleFunc("/metadata-check",func(w http.ResponseWriter,r *http.Request){req,_:=http.NewRequest("GET","http://169.254.169.254/computeMetadata/v1/",nil);req.Header.Set("Metadata-Flavor","Google");response,err:=(&http.Client{Timeout:2*time.Second}).Do(req);blocked:=err!=nil;if response!=nil {response.Body.Close()};json.NewEncoder(w).Encode(map[string]any{"blocked":blocked})})
 http.HandleFunc("/s3-check",func(w http.ResponseWriter,r *http.Request){
  bucket:=os.Getenv("S3_BUCKET");guard:=os.Getenv("READINESS_GUARD_BUCKET");body:=[]byte(nonce);key:="readiness-object";result:=map[string]any{}
  code,_,err:=s3("PUT",bucket,key,body);result["put"]=err==nil&&code==200
  code,data,err:=s3("GET",bucket,key,nil);result["get"]=err==nil&&code==200&&bytes.Equal(data,body)
  code,_,err=s3("DELETE",bucket,key,nil);result["delete"]=err==nil&&(code==200||code==204)
  code,_,err=s3("HEAD",guard,"",nil);result["guardReadDenied"]=err==nil&&code==403
  code,_,err=s3("PUT",guard,"forbidden",body);result["guardWriteDenied"]=err==nil&&code==403
  success:=true;for _,v:=range result {if v!=true {success=false}};if !success {w.WriteHeader(500)};json.NewEncoder(w).Encode(result)
 })
 port:=os.Getenv("PORT");if strings.TrimSpace(port)=="" {port="8080"};panic(http.ListenAndServe(":"+port,nil))
}
