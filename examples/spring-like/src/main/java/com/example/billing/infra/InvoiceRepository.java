package com.example.billing.infra;

import com.example.billing.domain.Invoice;

public class InvoiceRepository {
    public void save(Invoice invoice) {
        // persistence adapter; the domain never sees this class
    }
}
