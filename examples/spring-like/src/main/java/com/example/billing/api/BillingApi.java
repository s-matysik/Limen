package com.example.billing.api;

import com.example.billing.domain.Invoice;
import com.example.catalog.api.CatalogApi;

/** Public surface of the billing unit. */
public class BillingApi {
    private final CatalogApi catalog;

    public BillingApi(CatalogApi catalog) {
        this.catalog = catalog;
    }

    public Invoice invoiceFor(String sku) {
        Invoice invoice = new Invoice();
        invoice.addLine(catalog.findBySku(sku).price());
        return invoice;
    }
}
