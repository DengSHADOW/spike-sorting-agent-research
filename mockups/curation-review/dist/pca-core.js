/* Centered waveform PCA. No amplitude normalization or per-cluster refitting. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SpikePCA=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 function eigen(cov,n){
  const a=Float64Array.from(cov),v=new Float64Array(n*n);for(let i=0;i<n;i++)v[i*n+i]=1;
  const scale=Math.max(0,...Array.from({length:n},(_,i)=>Math.abs(a[i*n+i])));
  for(let sweep=0;sweep<80;sweep++){
   let maximum=0;
   for(let p=0;p<n;p++)for(let q=p+1;q<n;q++){
    const apq=a[p*n+q];maximum=Math.max(maximum,Math.abs(apq));if(Math.abs(apq)<=Math.max(scale*1e-13,1e-25))continue;
    const tau=(a[q*n+q]-a[p*n+p])/(2*apq),t=(tau>=0?1:-1)/(Math.abs(tau)+Math.sqrt(1+tau*tau)),c=1/Math.sqrt(1+t*t),s=t*c;
    const app=a[p*n+p],aqq=a[q*n+q];a[p*n+p]=app-t*apq;a[q*n+q]=aqq+t*apq;a[p*n+q]=a[q*n+p]=0;
    for(let k=0;k<n;k++){if(k!==p&&k!==q){const x=a[k*n+p],y=a[k*n+q];a[k*n+p]=a[p*n+k]=c*x-s*y;a[k*n+q]=a[q*n+k]=s*x+c*y;}const x=v[k*n+p],y=v[k*n+q];v[k*n+p]=c*x-s*y;v[k*n+q]=s*x+c*y;}
   }
   if(maximum<=Math.max(scale*1e-12,1e-24))break;
  }
  const order=Array.from({length:n},(_,i)=>i).sort((i,j)=>a[j*n+j]-a[i*n+i]||i-j),values=order.map(i=>Math.max(0,a[i*n+i])),vectors=order.slice(0,3).map(i=>{const x=Float64Array.from({length:n},(_,k)=>v[k*n+i]);let peak=0;for(let j=1;j<n;j++)if(Math.abs(x[j])>Math.abs(x[peak]))peak=j;if(x[peak]<0)for(let j=0;j<n;j++)x[j]*=-1;return x;});return {values,vectors};
 }
 function fit(waves,n,rows){
  if(!Number.isInteger(n)||n<2||waves.length%n||!rows.length)throw Error('PCA requires nonempty waveform rows');
  const mean=new Float64Array(n),cov=new Float64Array(n*n);for(const row of rows)for(let j=0;j<n;j++){const x=waves[row*n+j];if(!Number.isFinite(x))throw Error('Nonfinite PCA input');mean[j]+=x;}for(let j=0;j<n;j++)mean[j]/=rows.length;
  const centered=new Float64Array(n);for(const row of rows){for(let j=0;j<n;j++)centered[j]=waves[row*n+j]-mean[j];for(let j=0;j<n;j++)for(let k=0;k<=j;k++)cov[j*n+k]+=centered[j]*centered[k];}
  for(let j=0;j<n;j++)for(let k=0;k<=j;k++)cov[k*n+j]=cov[j*n+k]/=Math.max(1,rows.length-1);
  const {values,vectors}=eigen(cov,n),total=values.reduce((a,b)=>a+b,0);return {mean,vectors,variance:values.slice(0,3).map(v=>total?v/total:0),fit_n:rows.length};
 }
 function project(waves,n,model){const scores=new Float64Array(waves.length/n*3);for(let row=0;row<waves.length/n;row++)for(let pc=0;pc<model.vectors.length;pc++){let value=0;for(let j=0;j<n;j++)value+=(waves[row*n+j]-model.mean[j])*model.vectors[pc][j];scores[row*3+pc]=value;}return scores;}
 function inside(x,y,polygon){let yes=false;for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){const a=polygon[i],b=polygon[j];if((a[1]>y)!==(b[1]>y)&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])yes=!yes;}return yes;}
 return {eigen,fit,project,inside};
});
