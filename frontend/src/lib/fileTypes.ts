// Upload dialogs offer every spreadsheet flavour the backend can read
// (it works out the real type from the file's content, not its name).

/** Any Excel workbook, CSV/TSV, or an ERP HTML export. */
export const EXCEL_ACCEPT = '.xlsx,.xlsm,.xlsb,.xls,.xlt,.xltx,.csv,.tsv,.txt,.htm,.html'

/** Workbooks only, for files that need named sheets (no CSV). */
export const EXCEL_WORKBOOK_ACCEPT = '.xlsx,.xlsm,.xlsb,.xls,.xlt,.xltx'
