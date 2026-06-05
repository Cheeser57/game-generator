(define (problem legacy-of-stories)

    (:domain adventure-core)

    (:objects
        hero - player
        sapkowski_manor - room
        forest_clearing - room
        storyteller_journal - item
        golden_contract - item
        sapkowski - character
        old_storyteller - character
    )

    (:init
        ;; player location
        (at hero sapkowski_manor)

        ;; room connections
        (connected sapkowski_manor forest_clearing)
        (connected forest_clearing sapkowski_manor)

        ;; item locations
        (item-at storyteller_journal forest_clearing)
        (item-at golden_contract sapkowski_manor)

        ;; npc locations
        (npc-at sapkowski sapkowski_manor)
        (npc-at old_storyteller forest_clearing)

        ;; quest-defined path unlock item
        (path-key golden_contract)
    )

    (:goal
        (and
            (at hero forest_clearing)
            (talked-to old_storyteller)
            (item-at storyteller_journal forest_clearing)
        )
    )
)