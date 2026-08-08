package com.example.catalog.domain;

import com.example.shared.Money;

public class Product {
    private final String sku;
    private final Money price;

    public Product(String sku, Money price) {
        this.sku = sku;
        this.price = price;
    }

    public String sku() { return sku; }
    public Money price() { return price; }
}
