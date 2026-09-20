"""XBRL tag ladders. Data only - no logic lives here.

This is the file to edit when a company resolves to a wrong or missing figure.

One shape for every lookup: {field: {namespace: [rung, ...]}}, walked by the
single resolver in resolve.py. A rung is a string in a two-operator syntax:

    "TagA"              a single tag
    "TagA|TagB"         alternative spellings of one concept, widest first
    "TagA + TagB|TagC"  disjoint concepts summed

Rungs are tried in order and the first that resolves anything wins, so a rung
naming a total must come before the rung naming that total's components -
otherwise a filer publishing both gets the same money counted twice.
"""

from __future__ import annotations

# Tags that establish a balance sheet date. Whichever namespace carries the
# newest one decides how the whole company is read.
ANCHORS = {
    "us-gaap": ["Assets", "LiabilitiesAndStockholdersEquity", "Liabilities", "StockholdersEquity"],
    "ifrs-full": ["Assets", "EquityAndLiabilities", "Liabilities", "Equity"],
}

# Fields that make up the formula, in display order.
BSCF_FIELDS = ("cash", "short_term_investments", "long_term_investments",
               "debt_current", "debt_noncurrent")

# Short codes for the screen table's `gaps` column. A field reading zero can
# mean "genuinely none" or "we failed to find their tag", and only the filing
# settles which, so the run reports the ambiguity rather than hiding it.
GAP_CODES = {
    "short_term_investments": "sti",
    "long_term_investments": "lti",
    "debt_current": "dcur",
    "debt_noncurrent": "dnc",
}

INSTANT_FIELDS: dict[str, dict[str, list[str]]] = {
    "cash": {
        "us-gaap": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "Cash",
            "CashAndDueFromBanks",
        ],
        "ifrs-full": [
            "CashAndCashEquivalents",
            "Cash",
        ],
    },
    "short_term_investments": {
        "us-gaap": [
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
            "DebtSecuritiesAvailableForSaleCurrent",
            "AvailableForSaleSecuritiesCurrent",
            "OtherShortTermInvestments",
        ],
        "ifrs-full": [
            # Parent of the three measurement categories below; must come first.
            "CurrentFinancialAssets",
            "CurrentFinancialAssetsAtFairValueThroughProfitOrLossMandatorilyMeasuredAtFairValue"
            "|CurrentFinancialAssetsAtFairValueThroughProfitOrLoss"
            " + CurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome"
            "|CurrentFinancialAssetsMeasuredAtFairValueThroughOtherComprehensiveIncome"
            " + CurrentFinancialAssetsAtAmortisedCost",
            "OtherCurrentFinancialAssets",
        ],
    },
    "long_term_investments": {
        "us-gaap": [
            "LongTermInvestments",
            "MarketableSecuritiesNoncurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
            "DebtSecuritiesAvailableForSaleNoncurrent",
            "AvailableForSaleSecuritiesNoncurrent",
            "OtherLongTermInvestments",
        ],
        "ifrs-full": [
            "NoncurrentFinancialAssets",
            "NoncurrentFinancialAssetsAtFairValueThroughProfitOrLossMandatorilyMeasuredAtFairValue"
            "|NoncurrentFinancialAssetsAtFairValueThroughProfitOrLoss"
            " + NoncurrentFinancialAssetsMeasuredAtFairValueThroughOtherComprehensiveIncome"
            "|NoncurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome"
            " + NoncurrentFinancialAssetsAtAmortisedCost",
            "OtherNoncurrentFinancialAssets",
        ],
    },
    "debt_current": {
        "us-gaap": [
            "DebtCurrent",
            "CommercialPaper"
            " + ShortTermBorrowings|OtherShortTermBorrowings|ShortTermNonBankLoansAndNotesPayable"
            " + LongTermDebtCurrent|LongTermDebtAndCapitalLeaseObligationsCurrent"
            "|NotesPayableCurrent|SecuredDebtCurrent|ConvertibleNotesPayableCurrent"
            " + LinesOfCreditCurrent",
        ],
        "ifrs-full": [
            # The current-portion tag is the parent of any separately tagged
            # current bonds, so it is tried before them and never added to them.
            "CurrentPortionOfLongtermBorrowings"
            " + ShorttermBorrowings|CurrentBorrowings",
            "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued"
            " + ShorttermBorrowings|CurrentBorrowings",
        ],
    },
    "debt_noncurrent": {
        "us-gaap": [
            "LongTermDebtNoncurrent|LongTermDebtAndCapitalLeaseObligationsNoncurrent"
            "|NotesPayableNoncurrent|SecuredDebtNoncurrent|ConvertibleDebtNoncurrent"
            "|ConvertibleNotesPayableNoncurrent",
            "LongTermDebt|DebtLongtermAndShorttermCombinedAmount"
            "|LongTermDebtAndCapitalLeaseObligations",
        ],
        "ifrs-full": [
            "NoncurrentPortionOfNoncurrentBondsIssued"
            " + LongtermBorrowings|NoncurrentBorrowings",
            "Borrowings",
        ],
    },
    # Reported for context, never added to debt.
    "other_lt_liabilities": {
        "us-gaap": ["OtherLiabilitiesNoncurrent", "OtherLongTermLiabilities"],
        "ifrs-full": ["OtherNoncurrentLiabilities", "OtherNoncurrentFinancialLiabilities"],
    },
    "lease_liabilities": {
        "us-gaap": ["OperatingLeaseLiabilityNoncurrent + OperatingLeaseLiabilityCurrent"],
        "ifrs-full": ["LeaseLiabilities", "NoncurrentLeaseLiabilities + CurrentLeaseLiabilities"],
    },
    # Not printed as line items. Total assets normalizes the signal so filers in
    # different currencies can be ranked together; the two subtotals are
    # sanity ceilings that catch a debt figure counted twice.
    "total_assets": {
        "us-gaap": ["Assets"],
        "ifrs-full": ["Assets"],
    },
    "liabilities_current": {
        "us-gaap": ["LiabilitiesCurrent"],
        "ifrs-full": ["CurrentLiabilities"],
    },
    "liabilities_noncurrent": {
        "us-gaap": ["LiabilitiesNoncurrent"],
        "ifrs-full": ["NoncurrentLiabilities"],
    },
}

# Captions that bundle current maturities into a non-current figure. When one of
# these supplies the non-current line, the separately reported current portion is
# netted out so the two debt lines cannot describe the same dollars.
COMBINED_DEBT_TAGS = {
    "LongTermDebt",
    "DebtLongtermAndShorttermCombinedAmount",
    "LongTermDebtAndCapitalLeaseObligations",
    "Borrowings",
}

# --------------------------------------------------------------------------
# Sweep vocabulary
#
# The ladders above match exact tag names, so a filer using anything outside
# them reads as zero and the money vanishes silently. The sweep is the backstop:
# it matches on vocabulary instead of exact names, against whatever tags that
# filer actually used, turning an unrecognized tag into a quantified unknown. It
# needs no per-ticker maintenance as the universe grows.
# --------------------------------------------------------------------------

SWEEP_ASSET_PATTERNS = ("securities", "investment", "marketablesecurit")

# Borrowings vocabulary only. A borrowing the ladders did not consume is exactly
# the blind spot worth knowing about; deferred credits, accruals and other
# long-term liabilities are out of the formula by design rather than missed, and
# sweeping them would flag every company with a pension or a deferred tax
# balance. Tags the ladders consumed are excluded by name before matching.
SWEEP_LIABILITY_PATTERNS = (
    "debt", "borrow", "notespayable", "bondsissued", "bondspayable",
    "commercialpaper", "lineofcredit", "linesofcredit", "loanspayable",
    "convertible",
)

# Asset vocabulary that the liability patterns above would otherwise match: an
# available-for-sale debt security is something the company owns, not owes.
SWEEP_LIABILITY_EXCLUDE = ("securit", "available", "investment", "receivable", "heldto")

# What the sweep must never count. Grouped by the reason each entry is here -
# the groups were already in the comments, they just were not in the data, so a
# change could not target one reason without re-reading all of them.
#
# Matching is by substring, so an entry that contains another entry is dead
# weight: "unrealized" can never fire while "realized" is present, and
# "fairvaluedisclosure" can never fire while "fairvalue" is. Both were dropped.

# Disclosure-note restatements of a balance counted elsewhere: maturity
# ladders, gain/loss tables, cost-basis alternates, rate disclosures.
# Including these would multiply the same pool several times over.
_DISCLOSURE_ARTIFACTS = (
    "maturit", "realized", "impairment", "pledged", "amortizedcost",
    "fairvalue", "restricted", "continuous", "accumulated",
    "allowance", "creditloss", "weightedaverage", "interestrate", "proceeds",
    "repayments", "instrument", "unamortized", "unamortised", "covenant",
    "issuancecosts", "discount", "guarantee", "conversion", "redemption",
    "restructur", "extinguish", "arisingfrom", "effectivein",
)

# Current operating accruals, and combined captions that restate cash and
# investments already counted: out of scope rather than missed.
_NOT_IN_FORMULA = (
    "liabilitiescurrent",
    "cashcashequivalentsand",
)

# For a REIT or a BDC "investment" is the operating business - buildings and
# loan books - not a liquid balance. Without these a property trust flags its
# whole balance sheet and buries the real findings.
_OPERATING_INVESTMENTS = (
    "realestateinvestment", "investmentproperty", "investmentbuilding",
    "investmentowned", "netinvestmentinlease", "financialinstrumentsowned",
    "taxbasisofinvestments", "derivative",
)

# Not a balance on the sheet: repo funding is a liability despite the
# "securities" in its name, and a commitment is an undrawn future obligation.
_NOT_A_BALANCE = (
    "soldunderagreements",
    "commitment",
)

SWEEP_EXCLUDE_PATTERNS = (
    _DISCLOSURE_ARTIFACTS
    + _NOT_IN_FORMULA
    + _OPERATING_INVESTMENTS
    + _NOT_A_BALANCE
)

SWEEP_DETAIL_TAGS = 4  # source tags reported per side
