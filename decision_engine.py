def decision(score, rr, rsi, volumen, confluencia):

    if score <= 3:
        return "NO TOCAR"

    if rr < 3:
        return "RR BAJO"

    if volumen < 1:
        return "SIN VOLUMEN"

    if confluencia == "bajista":
        return "ESPERAR"

    if score >= 7 and rsi > 50:
        return "COMPRA FUERTE"

    if score >= 6:
        return "COMPRA"

    return "ESPERAR"
