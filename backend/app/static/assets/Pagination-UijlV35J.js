import{d,j as e,e as x}from"./index-C-SQNU-I.js";import{f as i}from"./AppShell-Ckw3QbbW.js";/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const v=d("ChevronFirst",[["path",{d:"m17 18-6-6 6-6",key:"1yerx2"}],["path",{d:"M7 6v12",key:"1p53r6"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const p=d("ChevronLast",[["path",{d:"m7 18 6-6-6-6",key:"lwmzdw"}],["path",{d:"M17 6v12",key:"1o0aio"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const k=d("ChevronLeft",[["path",{d:"m15 18-6-6 6-6",key:"1wnfg3"}]]);/**
 * @license lucide-react v0.469.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const w=d("ChevronRight",[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]]);function N(s,r){return r<=7?Array.from({length:r},(l,n)=>n+1):s<=4?[1,2,3,4,5,"right-ellipsis",r]:s>=r-3?[1,"left-ellipsis",r-4,r-3,r-2,r-1,r]:[1,"left-ellipsis",s-1,s,s+1,"right-ellipsis",r]}function _({page:s,pageCount:r,pageSize:l,totalItems:n,onPageChange:a,onPageSizeChange:m,itemLabel:o="items",pageSizeOptions:h=[5,10,25,50],className:b}){if(n===0)return null;const f=(s-1)*l+1,j=Math.min(s*l,n),u=N(s,r);return e.jsxs("nav",{"aria-label":`${o} pagination`,className:x("flex flex-col gap-3 rounded-2xl border border-border bg-surface/40 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between",b),children:[e.jsxs("div",{className:"flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint",children:[e.jsxs("span",{children:["Showing ",e.jsxs("strong",{className:"font-semibold text-ink",children:[i(f),"-",i(j)]})," of"," ",e.jsx("strong",{className:"font-semibold text-ink",children:i(n)})," ",o]}),e.jsxs("label",{className:"flex items-center gap-2",children:[e.jsx("span",{children:"Per page"}),e.jsx("select",{value:l,onChange:t=>m(Number(t.target.value)),"aria-label":`${o} per page`,className:"min-h-11 rounded-lg border border-border bg-surface px-2 text-xs font-semibold text-ink outline-none transition focus:border-accent sm:min-h-9",children:h.map(t=>e.jsx("option",{value:t,children:i(t)},t))})]})]}),e.jsxs("div",{className:"flex w-full items-center justify-between gap-1 sm:w-auto sm:justify-start sm:self-auto",children:[e.jsx(c,{label:"First page",disabled:s===1,onClick:()=>a(1),children:e.jsx(v,{className:"h-3.5 w-3.5"})}),e.jsx(c,{label:"Previous page",disabled:s===1,onClick:()=>a(s-1),children:e.jsx(k,{className:"h-3.5 w-3.5"})}),e.jsx("div",{className:"hidden items-center gap-1 sm:flex",children:u.map(t=>typeof t=="number"?e.jsx("button",{type:"button",onClick:()=>a(t),"aria-label":`Page ${t}`,"aria-current":t===s?"page":void 0,className:x("grid h-9 min-w-9 place-items-center rounded-lg px-2 text-xs font-semibold transition",t===s?"bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2":"text-ink-dim hover:bg-bg-soft hover:text-ink"),children:i(t)},t):e.jsx("span",{className:"grid h-8 min-w-6 place-items-center text-xs text-ink-faint",children:"…"},t))}),e.jsxs("span",{className:"px-2 text-xs font-semibold text-ink-dim sm:hidden",children:[i(s)," / ",i(r)]}),e.jsx(c,{label:"Next page",disabled:s===r,onClick:()=>a(s+1),children:e.jsx(w,{className:"h-3.5 w-3.5"})}),e.jsx(c,{label:"Last page",disabled:s===r,onClick:()=>a(r),children:e.jsx(p,{className:"h-3.5 w-3.5"})})]})]})}function c({label:s,disabled:r,onClick:l,children:n}){return e.jsx("button",{type:"button","aria-label":s,title:s,disabled:r,onClick:l,className:"grid h-11 w-11 place-items-center rounded-lg border border-transparent text-ink-dim transition hover:border-border hover:bg-bg-soft hover:text-ink disabled:pointer-events-none disabled:opacity-30 sm:h-9 sm:w-9",children:n})}export{_ as P};
