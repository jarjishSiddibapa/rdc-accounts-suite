"""The Accounts Sales Register query, exactly as supplied.

Nothing in it is changed except the date predicate (see below), so the rows come
back exactly as the ERP holds them. Its binds are :p_from_date, :p_to_date,
:p_org_id and the four optional filters (:p_trkinv, :p_location, :p_cust_name,
:p_cust_number), which are left empty so the report covers every location and
customer.

The date predicate takes an inclusive start and an exclusive midnight after the
inclusive end date, so transactions at any time on the end date are included.
"""

SALES_REGISTER_SQL = r"""
SELECT DISTINCT
    rbs.name                                                        source,
    rct.trx_number                                                  invoice_number,
    rct.comments                                                    comments,
    to_char(rct.trx_date, 'DD-MON-YYYY')                            invoice_date,
    rct.doc_sequence_value                                          document_number,
    rct.ct_reference                                                reference,
    rct.interface_header_attribute1,
    rct.interface_header_attribute2,
    rct.interface_header_attribute3,
    to_char(rcg.gl_date, 'DD-MON-YYYY')                             gl_date,
    to_char(rcg.gl_posted_date, 'DD-MON-YYYY')                      gl_posted_date,
    gcc.segment1
    || '.'
    || gcc.segment2
    || '.'
    || gcc.segment3
    || '.'
    || gcc.segment4
    || '.'
    || gcc.segment5
    || '.'
    || gcc.segment6
    || '.'
    || gcc.segment7                                                 gl_code_combination,
    tbl1.description                                                gl_code_desc,
    rcg.account_class                                               account_class,
    rct.purchase_order                                              po_number -- ood.organization_name oraganization_name, --Commented by Siddu
    ,
    (
        SELECT
            ood.name
        FROM
            apps.hr_organization_units ood
        WHERE
            TO_NUMBER(rct.interface_header_attribute10) = ood.organization_id
    )                                                               oraganization_name --Added by Siddu
    ,
    msi.segment1                                                    item_name,
    msi.description                                                 item_description,
    (
        CASE
            WHEN tbl1.description LIKE '%CGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%SGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%IGST%' THEN
                NULL
            ELSE
                nvl(rcl.quantity_invoiced, rcl.quantity_credited)
        END
    )                                                               quantity_invoiced,
    (
        CASE
            WHEN tbl1.description LIKE '%CGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%SGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%IGST%' THEN
                NULL
            ELSE
                rcl.unit_selling_price
        END
    )                                                               unit_selling_price,
    (
        CASE
            WHEN tbl1.description LIKE '%CGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%SGST%' THEN
                NULL
            WHEN tbl1.description LIKE '%IGST%' THEN
                NULL
            ELSE
                nvl(
                    nvl(rcl.quantity_invoiced, rcl.quantity_credited),
                    1
                ) * rcl.unit_selling_price
        END
    )                                                               line_amount,
    nvl((CASE
        WHEN tbl1.description LIKE '%CGST%' THEN
            NULL
        WHEN tbl1.description LIKE '%SGST%' THEN
            NULL
        WHEN tbl1.description LIKE '%IGST%' THEN
            NULL
        ELSE((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
    END),
        0)                                                          total_invoice_amount_wot,
    rcl.sales_order                                                 sales_order_num,
    rcl.sales_order_date                                            sales_order_date,
    hca.account_number                                              customer_account_number,
    hca.customer_class_code                                         customer_class_code,
    hp.party_name                                                   customer_name,
    (
        SELECT
            c.address1
            || ','
            || c.address2
            || ','
            || c.address3
            || ','
            || c.address4
            || ','
            || c.city
            || ','
            || c.state
            || ','
            || c.postal_code
            || ','
            || c.country
        FROM -- apps.hz_parties a, --commented by Siddu
            apps.hz_party_sites         b,
            apps.hz_locations           c,
            apps.hz_cust_accounts_all   d,
            apps.hz_cust_acct_sites_all e,
            apps.hz_cust_site_uses_all  f
        WHERE
                1 = 1 -- a.party_id = b.party_id --commented by Siddu
            AND b.location_id = c.location_id -- AND a.party_id = d.party_id --commented by Siddu
            AND e.party_site_id = b.party_site_id
            AND f.cust_acct_site_id = e.cust_acct_site_id
            AND d.cust_account_id = e.cust_account_id
            AND f.site_use_id = rct.ship_to_site_use_id
    )                                                               customer_ship_to,--Added by Prashant on 23-APR-18
    (
        SELECT
            c.address1
            || ','
            || c.address2
            || ','
            || c.address3
            || ','
            || c.address4
            || ','
            || c.city
            || ','
            || c.state
            || ','
            || c.postal_code
            || ','
            || c.country
        FROM -- apps.hz_parties a, --commented by Siddu
            apps.hz_party_sites         b,
            apps.hz_locations           c,
            apps.hz_cust_accounts_all   d,
            apps.hz_cust_acct_sites_all e,
            apps.hz_cust_site_uses_all  f
        WHERE
                1 = 1 -- a.party_id = b.party_id --commented by Siddu
            AND b.location_id = c.location_id -- AND a.party_id = d.party_id --commented by Siddu
            AND e.party_site_id = b.party_site_id
            AND f.cust_acct_site_id = e.cust_acct_site_id
            AND d.cust_account_id = e.cust_account_id
            AND f.site_use_id = rct.bill_to_site_use_id
    )                                                               customer_bill_to,
    rtt.name                                                        transaction_name,
    decode(rtt.type, 'INV', 'INVOICE', 'CM', 'Credit Memo',
           'DM', 'Debit Memo')                                      transaction_type,
    decode(rcl.line_type, 'LINE', 'LINE_TOTAL', 'TAX', 'TOTAL_TAX') line_type, -- fu.user_name user_name , --commented by Siddu
    (
        SELECT
            fu.user_name
        FROM
            apps.fnd_user fu
        WHERE
            fu.user_id = rct.last_updated_by
    )                                                               user_name --Added by Siddu
    ,
    (
        SELECT
            tax_rate_code
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%CGST%'
    )                                                               cgst,
    (
        SELECT
            tax_rate_code
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%SGST%'
    )                                                               sgst,
    (
        SELECT
            tax_rate_code
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%IGST%'
    )                                                               igst,
    (
        SELECT
            tax_rate_code
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%TCS%'
    )                                                               tcs --Added by Srijan 25 JAN 2021
    ,
    nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%CGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%CGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    )                                                               cgst_amt,
    nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%SGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%SGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    )                                                               sgst_amt,
    nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%IGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%IGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    )                                                               igst_amt,
    nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%TCS%'
        ),
            CASE
                WHEN tbl1.description LIKE '%TCS%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    )                                                               tcs_amt --Added by Srijan 25 JAN 2021
    ,
    nvl((CASE
        WHEN tbl1.description LIKE '%CGST%' THEN
            NULL
        WHEN tbl1.description LIKE '%SGST%' THEN
            NULL
        WHEN tbl1.description LIKE '%IGST%' THEN
            NULL
        WHEN tbl1.description LIKE '%TCS%' THEN
            NULL --Added by Srijan 27-JAN-2021
        ELSE((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
    END),
        0) + nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%CGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%CGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    ) + nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%SGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%SGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    ) + nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
                AND tax_rate_code LIKE '%IGST%'
        ),
            CASE
                WHEN tbl1.description LIKE '%IGST%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    ) + nvl(
        nvl((
            SELECT
                rounded_tax_amt_fun_curr
            FROM
                apps.jai_tax_lines jtl
            WHERE
                    jtl.trx_line_id = rcl.customer_trx_line_id
                AND jtl.entity_code = 'TRANSACTIONS'
                AND tax_rate_code LIKE '%TCS%'
        ),
            CASE
                WHEN tbl1.description LIKE '%TCS%' THEN
                    ((decode(rct.invoice_currency_code, 'INR', rcg.amount * 1, rcg.amount * rct.exchange_rate)))
            END
        ),
        0
    ) --Added By Srijan 27-JAN-2020
                                                                   total_line_amount /* ,(CASE WHEN TBL2.tax_rate_percentage LIKE '%CGST%' THEN TBL2.tax_rate_percentage END) cgst_cent ,(CASE WHEN TBL2.tax_rate_percentage LIKE '%SGST%' THEN TBL2.tax_rate_percentage END) sgst_cent ,(CASE WHEN TBL2.tax_rate_percentage LIKE '%IGST%' THEN TBL2.tax_rate_percentage END) igst_cent*/
    ,
    (
        SELECT
            tax_rate_percentage
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%CGST%'
    )                                                               cgst_cent,
    (
        SELECT
            tax_rate_percentage
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%SGST%'
    )                                                               sgst_cent,
    (
        SELECT
            tax_rate_percentage
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND tax_rate_code LIKE '%IGST%'
    )                                                               igst_cent,
    (
        SELECT
            tax_rate_percentage
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS'
            AND tax_rate_code LIKE '%TCS%'
    )                                                               tcs_cent --Added By Srijan 25-JAN-2021
    ,     /* ( SELECT gcck.segment1 FROM apps.ra_cust_trx_line_gl_dist_all rclgd, apps.gl_code_combinations_kfv gcck
    WHERE rclgd.code_combination_id = gcck.code_combination_id
    AND rclgd.customer_trx_line_id = rcl.customer_trx_line_id
    AND rclgd.account_class = 'REV' GROUP BY rclgd.customer_trx_line_id, gcck.segment1 ) segment1,*/ --commented by Siddu
    (
        SELECT
            gcck.segment1
        FROM
            apps.gl_code_combinations_kfv gcck
        WHERE
            gcck.code_combination_id = (
                SELECT
                    rclgd.code_combination_id
                FROM
                    apps.ra_cust_trx_line_gl_dist_all rclgd
                WHERE
                        rclgd.customer_trx_line_id = rcl.customer_trx_line_id
                    AND rclgd.account_class = 'REV'
                    AND ROWNUM < 2
            )
    )                                                               segment1,--Added by Siddu
/*( SELECT DISTINCT third_party_primary_reg_num FROM apps.jai_tax_lines jtl
WHERE jtl.trx_line_id = rcl.customer_trx_line_id AND third_party_primary_reg_name = 'GST' ) cust_gst_num, */ --commented by Siddu
    (
        SELECT
            third_party_primary_reg_num
        FROM
            apps.jai_tax_lines jtl
        WHERE
                jtl.trx_line_id = rcl.customer_trx_line_id
            AND jtl.entity_code = 'TRANSACTIONS' --Added By Srijan 4-Jun-2020
            AND third_party_primary_reg_name = 'GST'
            AND ROWNUM = 1
    )                                                               cust_gst_num,--Added by Siddu
    (
        SELECT
            nvl(jtdfl.hsn_code, jtdfl.sac_code) code
        FROM
            apps.jai_tax_det_fct_lines_v jtdfl
        WHERE
                jtdfl.trx_line_id = rcl.customer_trx_line_id
            AND jtdfl.trx_id = rct.customer_trx_id --Added by Srijan 4 JAN 2022
            AND organization_id IS NOT NULL
            AND location_id IS NOT NULL
            AND ROWNUM < 2
    )                                                               code --Added Rownum By Srijan 4-Jun-2020
    ,
    rcg.cust_trx_line_gl_dist_id,
    ffv.flex_value                                                  location --Added by Abhinav on 22-NOV-2022 --
    ,
    ffv.description, /* (SELECT DISTINCT name FROM apps.ra_salesreps_all WHERE salesrep_id = rct.primary_salesrep_id ) sales_per */ --commented by Siddu
    (
        SELECT
            name
        FROM
            apps.ra_salesreps_all
        WHERE
                salesrep_id = rct.primary_salesrep_id
            AND ROWNUM = 1
    )                                                               sales_per --Added by Siddu
    ,
    nvl((
        SELECT
            jtl.tax_invoice_num
        FROM
            apps.jai_tax_lines jtl
        WHERE
                trx_id = rct.customer_trx_id
            AND trx_line_id = rcl.customer_trx_line_id
            AND jtl.first_party_primary_reg_name = 'GST' --Added by Srijan 4 MAR 2021
            AND rcl.line_type = 'LINE'
            AND ROWNUM = 1
    ), jtdf.tax_invoice_num)                                        tax_invoice_num,
	(select EWBNO from apps.XXRDC_EINV_AR_IRN_HDR_TBL where trx_number = jtdf.tax_invoice_num and rownum = 1 ) EWBILL,
	nvl(rct.attribute10,(select IRN from apps.XXRDC_EINV_AR_IRN_HDR_TBL where trx_number = jtdf.tax_invoice_num and rownum = 1))                                                 irn_number --Added By Srijan 14-Oct-2020
    ,rct.attribute11                                                 ackn_number --Added By Srijan 14-Oct-2020
    ,
    rct.attribute12                                                 ackn_date, --Added By Srijan 14-Oct-2020 ,
  -- (select IRN from apps.XXRDC_EINV_AR_IRN_HDR_TBL where trx_number = jtdf.tax_invoice_num and rownum = 1 ) irn_Number1 -- Added By atish for SR170078


    /*(SELECT DISTINCT REGISTRATION_NUMBER FROM apps.JAI_PARTY_REGS JPR,
            apps.JAI_PARTY_REG_LINES JPRL, apps.org_organization_definitions ood,
            apps.fnd_flex_values_vl ffv2 WHERE JPRL.PARTY_REG_ID = JPR.PARTY_REG_ID
            AND JPR.PARTY_ID = ood.ORGANIZATION_ID AND ffv2.FLEX_VALUE_SET_ID = 1009917
            and ffv2.description = ood.ORGANIZATION_NAME AND ffv2.FLEX_VALUE = gcc.segment3
            AND JPR.PARTY_TYPE_CODE = 'IO' -- AND JPR.PARTY_ID = 1385 )*/ -- Added by Naiyar on 17-Aug-2023
    nvl( (SELECT DISTINCT
            first_party_primary_reg_num
        FROM
            apps.jai_tax_lines
        WHERE
                trx_id = rct.customer_trx_id
            AND trx_line_id = rcl.customer_trx_line_id
            AND trx_number = rct.trx_number
            AND first_party_primary_reg_num IS NOT NULL --Added by Nagesh on 07-JUl-25 to remove GSTIN duplicacy
            AND application_id = 222 --Added by Nagesh on 07-JUl-25 to remove GSTIN duplicacy
            AND tax_regime_id = 10000 --Added by Nagesh on 07-JUl-25 to remove GSTIN duplicacy
    ), (SELECT DISTINCT REGISTRATION_NUMBER
    FROM apps.JAI_PARTY_REGS JPR, apps.JAI_PARTY_REG_LINES JPRL
    , apps.org_organization_definitions ood
    , apps.fnd_flex_values_vl ffv2
    WHERE JPRL.PARTY_REG_ID = JPR.PARTY_REG_ID
    AND JPR.PARTY_ID = ood.ORGANIZATION_ID
    AND ffv2.FLEX_VALUE_SET_ID = 1009917
    and ffv2.description = ood.ORGANIZATION_NAME
    AND ffv2.FLEX_VALUE = gcc.segment3
    AND JPR.PARTY_TYPE_CODE = 'IO'
    and rct.trx_date between effective_from and nvl(effective_to,sysdate)
    and regime_id = 10000))                                                              gstin -- Added by ATISH on 02-JUN-2025                                                  gstin -- Added by ATISH on 02-JUN-2025
FROM
    apps.ra_customer_trx_all          rct,
    apps.ra_customer_trx_lines_all    rcl,
    apps.ra_cust_trx_line_gl_dist_all rcg,
    apps.jai_tax_det_factors          jtdf, -- apps.org_organization_definitions ood, --commented by Siddu
    apps.hz_cust_accounts             hca,
    apps.hz_parties                   hp,
    apps.ra_cust_trx_types_all        rtt, -- apps.fnd_user fu, --commented by Siddu
    apps.mtl_system_items             msi, -- apps.hz_parties hp1, --commented by Siddu -- apps.hz_parties hp2, --commented by Siddu
    apps.hz_cust_site_uses_all        hcsua,
    apps.gl_code_combinations         gcc,
    apps.ra_batch_sources_all         rbs,
    apps.fnd_flex_values_vl           ffv --Added by Abhinav on 22-NOV-2022
    ,
    apps.fnd_flex_value_sets          ffvs,
    apps.hz_party_sites               hps -- Added By KPMG 05-AUG-2024
    ,
    apps.hz_cust_acct_sites_all       hcsa -- Added By KPMG 05-AUG-2024
    ,
    (
        SELECT
            description,
            flex_value
        FROM
            apps.fnd_flex_values_vl
        WHERE
            flex_value_set_id = 1009919
    )   tbl1
    /* ,(SELECT tax_rate_code,rounded_tax_amt_fun_curr,tax_rate_percentage,trx_line_id FROM apps.jai_tax_lines jtl) TBL2 */
WHERE
        1 = 1 --AND rct.customer_trx_id =1872057
    AND tbl1.flex_value = gcc.segment5
    AND ffv.flex_value = gcc.segment3 --Added by Abhinav on 22-NOV-2022
    AND ffvs.flex_value_set_name = 'RDCLocation' --Added by Nagesh on 07-Jul-25
    AND ffvs.flex_value_set_id = ffv.flex_value_set_id --Added by Nagesh on 07-Jul-25
    -- AND TBL2.trx_line_id=rcl.customer_trx_line_id
    AND rct.customer_trx_id = rcg.customer_trx_id
    AND rcl.customer_trx_line_id = rcg.customer_trx_line_id
    AND rct.bill_to_customer_id = hca.cust_account_id
    AND rcl.customer_trx_line_id = jtdf.trx_line_id (+)
    AND rct.customer_trx_id = jtdf.trx_id --Added by Srijan 18 FEB 2021
    AND hca.party_id = hp.party_id -- AND rct.last_updated_by = fu.user_id --commented by Siddu
    AND rct.cust_trx_type_id = rtt.cust_trx_type_id -- AND to_number(rct.interface_header_attribute10) = ood.organization_id (+) --commented by Siddu
    AND rct.bill_to_site_use_id = hcsua.site_use_id -- Added By KPMG 05-AUG-2024
    AND hcsua.cust_acct_site_id = hcsa.cust_acct_site_id
    AND hca.cust_account_id = hcsa.cust_account_id
    AND hcsa.party_site_id = hps.party_site_id
   -- and jtdf.tax_invoice_num in ('7MU25BP1-7704','7MU25BP1-7693','5MU25BP2-3217')
    AND nvl(hps.attribute1, 'NA') = nvl(:p_trkinv,nvl(hps.attribute1, 'NA')) -- Added By KPMG 05-AUG-2024
    -- AND rct.ship_to_customer_id = hp1.party_id (+) --commented by Siddu -- AND rct.bill_to_customer_id = hp2.party_id (+) --commented by Siddu
   --AND rcg.gl_date BETWEEN '01-JUN-25' and '20-JUN-25'
 AND rcg.gl_date >= :p_from_date AND rcg.gl_date < :p_to_date + 1
   -- AND gcc.segment3 = Nvl(:p_location, gcc.segment3)
    AND ( (ffv.flex_value in (select ffvc.FLEX_VALUE from apps.fnd_flex_value_children_v ffvc
    where ffvc.PARENT_FLEX_VALUE= nvl(:p_location,ffvc.PARENT_FLEX_VALUE)))
    OR (ffv.flex_value = nvl(:p_location,ffv.flex_value)) ) --Added by Abhinav on 22-NOV-2022
    AND hp.party_id = nvl(:p_cust_name, hp.party_id)
    AND hca.cust_account_id = nvl(:p_cust_number, hca.cust_account_id)
    AND rcg.code_combination_id = gcc.code_combination_id
    AND rcl.inventory_item_id = msi.inventory_item_id (+)
    AND rcg.account_class = 'REV'
    AND rct.org_id = :p_org_id
    AND rbs.batch_source_id = rct.batch_source_id
    AND rbs.org_id = :p_org_id
    AND rct.org_id = rbs.org_id
    AND rct.complete_flag = 'Y'
    --AND rct.trx_number = '3233526';
"""
