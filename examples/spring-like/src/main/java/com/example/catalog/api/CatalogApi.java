package com.example.catalog.api;

import com.example.catalog.domain.Product;
import com.example.shared.Money;

/** Public surface of the catalog unit. */
public interface CatalogApi {
    Product findBySku(String sku);

    static Money listPrice(Product product) {
        return product.price();
    }
}
