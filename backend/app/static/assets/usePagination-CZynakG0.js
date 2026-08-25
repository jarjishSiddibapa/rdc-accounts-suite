import{c as l}from"./cn-5xcaR4Ep.js";import{r as a}from"./index-CVloMrSv.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const S=l("RotateCcw",[["path",{d:"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",key:"1357e3"}],["path",{d:"M3 3v5h5",key:"1xhq8a"}]]);function P(t,i=10,r){const[c,n]=a.useState(1),[o,f]=a.useState(i),e=Math.max(1,Math.ceil(t.length/o)),u=Math.min(c,e),s=t.length===0?0:(u-1)*o,g=Math.min(s+o,t.length);a.useEffect(()=>{n(1)},[r]),a.useEffect(()=>{c>e&&n(e)},[c,e]);const p=a.useMemo(()=>t.slice(s,g),[t,s,g]);function M(h){n(Math.min(Math.max(1,h),e))}function d(h){f(h),n(1)}return{page:u,pageCount:e,pageSize:o,totalItems:t.length,startIndex:s,endIndex:g,pagedItems:p,setPage:M,setPageSize:d}}export{S as R,P as u};
