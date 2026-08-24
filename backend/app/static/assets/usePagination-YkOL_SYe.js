import{d as l,r as a}from"./index-DDzSfSAp.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const S=l("RotateCcw",[["path",{d:"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",key:"1357e3"}],["path",{d:"M3 3v5h5",key:"1xhq8a"}]]);function P(t,i=10,f){const[c,n]=a.useState(1),[s,p]=a.useState(i),e=Math.max(1,Math.ceil(t.length/s)),u=Math.min(c,e),o=t.length===0?0:(u-1)*s,g=Math.min(o+s,t.length);a.useEffect(()=>{n(1)},[f]),a.useEffect(()=>{c>e&&n(e)},[c,e]);const r=a.useMemo(()=>t.slice(o,g),[t,o,g]);function M(h){n(Math.min(Math.max(1,h),e))}function d(h){p(h),n(1)}return{page:u,pageCount:e,pageSize:s,totalItems:t.length,startIndex:o,endIndex:g,pagedItems:r,setPage:M,setPageSize:d}}export{S as R,P as u};
