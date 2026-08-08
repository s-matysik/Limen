package com.example.shared;

/** Value object shared by every unit. */
public record Money(long cents, String currency) {
    public Money add(Money other) {
        return new Money(cents + other.cents, currency);
    }
}
