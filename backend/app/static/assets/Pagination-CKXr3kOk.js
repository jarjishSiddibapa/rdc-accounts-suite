import{c,a as x}from"./cn-BJWlDrkP.js";import{j as e}from"./index-QsuM4Rrh.js";import{f as i}from"./AppShell-t0jV3HFJ.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const u=c("ChevronFirst",[["path",{d:"m17 18-6-6 6-6",key:"1yerx2"}],["path",{d:"M7 6v12",key:"1p53r6"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const v=c("ChevronLast",[["path",{d:"m7 18 6-6-6-6",key:"lwmzdw"}],["path",{d:"M17 6v12",key:"1o0aio"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const k=c("ChevronLeft",[["path",{d:"m15 18-6-6 6-6",key:"1wnfg3"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const w=c("ChevronRight",[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const R=c("RotateCcw",[["path",{d:"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",key:"1357e3"}],["path",{d:"M3 3v5h5",key:"1xhq8a"}]]);function y(s,t){return t<=7?Array.from({length:t},(l,n)=>n+1):s<=4?[1,2,3,4,5,"right-ellipsis",t]:s>=t-3?[1,"left-ellipsis",t-4,t-3,t-2,t-1,t]:[1,"left-ellipsis",s-1,s,s+1,"right-ellipsis",t]}function L({page:s,pageCount:t,pageSize:l,totalItems:n,onPageChange:a,onPageSizeChange:h,itemLabel:d="items",pageSizeOptions:m=[5,10,25,50],className:b}){if(n===0)return null;const f=(s-1)*l+1,j=Math.min(s*l,n),p=y(s,t);return e.jsxs("nav",{"aria-label":`${d} pagination`,className:x("flex flex-col gap-3 rounded-2xl border border-border bg-surface/40 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between",b),children:[e.jsxs("div",{className:"flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint",children:[e.jsxs("span",{children:["Showing ",e.jsxs("strong",{className:"font-semibold text-ink",children:[i(f),"-",i(j)]})," of"," ",e.jsx("strong",{className:"font-semibold text-ink",children:i(n)})," ",d]}),e.jsxs("label",{className:"flex items-center gap-2",children:[e.jsx("span",{children:"Per page"}),e.jsx("select",{value:l,onChange:r=>h(Number(r.target.value)),"aria-label":`${d} per page`,className:"min-h-11 rounded-lg border border-border bg-surface px-2 text-xs font-semibold text-ink outline-none transition focus:border-accent sm:min-h-9",children:m.map(r=>e.jsx("option",{value:r,children:i(r)},r))})]})]}),e.jsxs("div",{className:"flex w-full items-center justify-between gap-1 sm:w-auto sm:justify-start sm:self-auto",children:[e.jsx(o,{label:"First page",disabled:s===1,onClick:()=>a(1),children:e.jsx(u,{className:"h-3.5 w-3.5"})}),e.jsx(o,{label:"Previous page",disabled:s===1,onClick:()=>a(s-1),children:e.jsx(k,{className:"h-3.5 w-3.5"})}),e.jsx("div",{className:"hidden items-center gap-1 sm:flex",children:p.map(r=>typeof r=="number"?e.jsx("button",{type:"button",onClick:()=>a(r),"aria-label":`Page ${r}`,"aria-current":r===s?"page":void 0,className:x("grid h-9 min-w-9 place-items-center rounded-lg px-2 text-xs font-semibold transition",r===s?"bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2":"text-ink-dim hover:bg-bg-soft hover:text-ink"),children:i(r)},r):e.jsx("span",{className:"grid h-8 min-w-6 place-items-center text-xs text-ink-faint",children:"…"},r))}),e.jsxs("span",{className:"px-2 text-xs font-semibold text-ink-dim sm:hidden",children:[i(s)," / ",i(t)]}),e.jsx(o,{label:"Next page",disabled:s===t,onClick:()=>a(s+1),children:e.jsx(w,{className:"h-3.5 w-3.5"})}),e.jsx(o,{label:"Last page",disabled:s===t,onClick:()=>a(t),children:e.jsx(v,{className:"h-3.5 w-3.5"})})]})]})}function o({label:s,disabled:t,onClick:l,children:n}){return e.jsx("button",{type:"button","aria-label":s,title:s,disabled:t,onClick:l,className:"grid h-11 w-11 place-items-center rounded-lg border border-transparent text-ink-dim transition hover:border-border hover:bg-bg-soft hover:text-ink disabled:pointer-events-none disabled:opacity-30 sm:h-9 sm:w-9",children:n})}export{L as P,R};
