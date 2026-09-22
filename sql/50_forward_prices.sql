-- Forward settlement prices by contract month, one row per (date, strip).
--
-- Long rather than wide on purpose: the pivot belongs in pandas, so a new
-- contract month appears on its own instead of needing another CASE arm.
-- XCU is the AB power exchange code.
SELECT  EffectiveDate,
        strip,
        price
FROM    Warehouse.dbo.ForwardPrices
WHERE   ExchangeCode = 'XCU'
  AND   price IS NOT NULL
  AND   EffectiveDate >= DATEADD(day, -{DAYS}, GETDATE())
ORDER BY EffectiveDate, strip;
