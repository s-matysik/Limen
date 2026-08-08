package com.example.billing.domain;

import com.example.shared.Money;

public class Invoice {
    private Money total = new Money(0, "EUR");

    public void addLine(Money amount) {
        total = total.add(amount);
    }

    public Money total() { return total; }
}
