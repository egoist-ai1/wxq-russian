"""Override пакета 10 (Lua): перенос русского текста по пробелам, офлайн.

Подмена TextAsset правил переноса не подействовала и вредит чату (TalkHelper режет слова), поэтому перенос делает
Lua-помощник `source/game-wordwrap/ru-space-wrap.lua`, вставленный в конкретные AutoBind-модули пакета 10. Каждая правка —
вызов помощника сразу после штатного присваивания `self.<поле>.text = arg` в setter и восстановление поля в
`vOnResourceUnLoaded`; остальные байты ресурса не меняются (обратная правка восстанавливает оригинал побайтно).

Общее покрытие (v12c): остальные AutoBind-модули с полем TimiText (source/game-wordwrap/coverage-targets.json, строит
`gen_wrap_targets.py --write-coverage`) получают не помощник, а один короткий вызов `_G._AceRuWrap` после того же присваивания;
сам помощник лежит ОДИН раз в общем чанке UIPrefabClass (базовый UI-класс, выполняется раньше любого UI-модуля) и выставляет
_G._AceRuWrap/_G._AceRuWrapRestore. Нет помощника в _G или он бросает исключение - поле остаётся со штатным текстом.

Читает установленную игру только на чтение (оригиналы пакета 10 сверяются с SHA из pilot_contract.LUA_REPLACEMENT_ALLOWED),
пишет кандидата `build/lua-wrap-<дата>-<id>/` (контракт wxq-pilot-carrier/v1, kind=lua-script) через тот же упаковщик и
нативную проверку SDK, что и build_carrier.py. Установку делает game_layer_deploy.py; здесь ничего не ставится.

`--diag-marker` — диагностическая сборка: к заголовку окна подсказок настроек (и к имени умения в карточке героя) дописывается
«·» (Lua выполнился), «··» (помощник принял поле) или «·<буква>» (помощник отказал, буква — причина, см. _AceRuReasonCodes).

`--diag-log` — диагностическая сборка журнала (не путать с --diag-marker): после помощника в каждый чанк дописывается слой
`source/game-wordwrap/ru-space-wrap-diag.lua` (сам помощник и его SHA не меняются). Слой пишет в `%TEMP%\\ace-ru-diag.log`
строки chunk/call/err (только числа и коды); в слой для каждого ресурса подставляются имя модуля и id ресурса.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_carrier as bc  # noqa: E402
import build_header_layout_patch as header_layout  # noqa: E402
import build_pilot_carrier as bp  # noqa: E402
import pilot_contract  # noqa: E402
import wxq_install  # noqa: E402

PACKAGE = '10'
HELPER = PROJECT / 'source/game-wordwrap/ru-space-wrap.lua'
DIAG_LAYER = PROJECT / 'source/game-wordwrap/ru-space-wrap-diag.lua'
DIAG_MODULE_PLACEHOLDER = '@@AceRuDiagModule@@'
DIAG_RESOURCE_PLACEHOLDER = '@@AceRuDiagResource@@'
MARKER = pilot_contract.LUA_PAYLOAD_MARKER.decode('ascii')
# ресурс -> (модуль AutoBind, поля TimiText, которые переносит помощник). Только поля с точкой присваивания `self.<поле>.text = arg`.
TARGETS = {
    'e607e142cfd91df0': ('UICardDetailHeroSkillClass_AutoBind', ('newSkillDescription', 'oldSkillDescription')),
    'aeff176f093cf41d': ('UICommonMSGBoxSettingsitemClass_AutoBind', ('title', 'infoTxt1', 'infoTxt2')),
    '99527bd5e8d17767': ('UICardDetailSkillDesItemClass_AutoBind', ('skillsDescription',)),
    '1edf1b6871d8dc7a': ('UICardSkillDescriptionItemClass_AutoBind', ('desc',)),
    '3b4c55d975d49ceb': ('UIHeroSkillDescriptionClass_AutoBind', ('description',)),
    '38340509a07bbcbb': ('UIHeroEnrtyDescriptionItemClass_AutoBind', ('description',)),
    '50c290acc2a3b19e': ('UICardExplanationItemClass_AutoBind', ('description',)),
    '6fb7291c59f144ae': ('UICardInfoLevelDesItemClass_AutoBind', ('effect',)),
    'c3dcb26ae530e158': ('UIGameAtlasCardInfoLevelDesItemClass_AutoBind', ('effect',)),
    '17e2f8afb42b56dd': ('UIHeroSimpleDetailTipsClass_AutoBind', ('introduced',)),
    '3d24ad9cb9eabd8e': ('UIGameAtlasSingleDesItemClass_AutoBind', ('description',)),
    '2a2654f5fff909c7': ('UIGameAtlasHeroSkillItemClass_AutoBind', ('content',)),
    '13d839f568c430c7': ('UIHeroCollectionSkillItemClass_AutoBind', ('content',)),
    'aedf6ed2bd9f27e0': ('UICardTipsLordSkillEffectItemClass_AutoBind', ('desc',)),
    'c15e3fb56b8b9247': ('UICardTipsTextContentItemClass_AutoBind', ('contentOne', 'contentTwo')),
    'a5b5ad814db94733': ('UITipsCardsItemClass_AutoBind', ('description',)),
    'd2f6d143fa8bdfb1': ('UICardDescForPandoraClass_AutoBind', ('desc',)),
    '27bd54216c836e13': ('UIPreviewEffectDetailsItemClass_AutoBind', ('description',)),
    'a2f4ecf20a9830a0': ('UIPreviewEffectNormalDetailsItemClass_AutoBind', ('description',)),
    '34ce6be147969f53': ('UIEmbattleLordSkillDescItemClass_AutoBind', ('description',)),
    '4e54e09e1dec2d2c': ('UILordSkillDescItemClass_AutoBind', ('content',)),
    '24a5bdab872c8d59': ('UILordSkillInfoItemClass_AutoBind', ('content',)),
    '9f0a1c328fcf7760': ('UICardLevelUpEffectItemClass_AutoBind', ('content',)),
    # --- расширение на остальные экраны (13.10.2026): setters AutoBind экранов событий, заданий, достижений, новостей, друзей, гайдов, энциклопедии, инвентаря ---
    '0138613d8915af8f': ('UINewBieGuideEnergyStepTextItemClass_AutoBind', ('description',)),
    '03e7760ff85fee7d': ('UIFriendAuthorizationPopClass_AutoBind', ('TipsTxt',)),
    '1432abb117a1d103': ('UINewbieGuideTipCenter2Class_AutoBind', ('description',)),
    '14dec121f99f3c13': ('UIOperationTeamInviteClass_AutoBind', ('description',)),
    '14e7c1708f5def42': ('UIBagDetailClass_AutoBind', ('information',)),
    '18fa377c5b87a9ee': ('UIOperationGuiLaiTiaoZhanSlapPageClass_AutoBind', ('description',)),
    '1d64e5a6b3a66d14': ('UIGameAtlasFlowItemClass_AutoBind', ('textContent',)),
    '392a9662d3a36d50': ('UINewbieGuideItem2Class_AutoBind', ('TxtContent',)),
    '39422ecbfe2e5839': ('UIOperationGuiLaiTiaoZhanHotLineup2Class_AutoBind', ('description',)),
    '3f6e43f0668755c2': ('UIBPTaskItemClass_AutoBind', ('questContentZeroone', 'questContentZerotwo')),
    '450b988136ab07b3': ('UIOperationChessPlayerThemePage3Class_AutoBind', ('chessPlayerDescription',)),
    '4aa6c8da11cb1608': ('UINewBieGuideKeywordItem3Class_AutoBind', ('description',)),
    '5205c92e6c7933fc': ('UIOmniCommissionNewPlayerGuideClass_AutoBind', ('sendMessageContent',)),
    '5548fa4e1a9a179c': ('UIOperationSpacialWishCardClass_AutoBind', ('taskContent',)),
    '55a86aea2f2f19b8': ('UIAchieveListPageClass_AutoBind', ('achievementDescription',)),
    '5737770b9be55ab9': ('UIAchieveSettleItemClass_AutoBind', ('achievementDescription',)),
    '5e99f2b9f0963c0b': ('UIOperationServerLaunchProgressAwardBigItemClass_AutoBind', ('requirements',)),
    '5f3ad2b974bac90b': ('UIOperationServerLaunchProgressMainPageClass_AutoBind', ('description',)),
    '60cc4255b843c5e7': ('UIFriendItemStyle6Class_AutoBind', ('applicationContent',)),
    '65f2752499035c96': ('UINewbieGuideItem4Class_AutoBind', ('description',)),
    '67201165cf99d8d7': ('UIAchievePackageTipsClass_AutoBind', ('condition',)),
    '69570ba346920851': ('UIFriendGiftChoiceItemClass_AutoBind', ('Txt_Des',)),
    '6cc65a1022de08a2': ('UIAchieveRecieveSingleAchieveItemClass_AutoBind', ('description',)),
    '739e32112b5b40a3': ('UIPiggyBankTaskRewardItemClass_AutoBind', ('description',)),
    '7cc621c296da0290': ('UINoticePanelClass_AutoBind', ('theContentsOfTheAnnouncement',)),
    '8ccbf5a07eafa0b2': ('UIGameAtlasOSHItemClass_AutoBind', ('description',)),
    '90ac9cb9560b114b': ('UIMailDetailsClass_AutoBind', ('emailContent',)),
    '938e29efdf873b9d': ('UINewBieGuideEnergyCustomizationItemClass_AutoBind', ('energyDescription',)),
    '977975966686d5a4': ('UIIntimateRewardItemClass_AutoBind', ('textDescription',)),
    '9a5efe219d464e13': ('UINewbieGuideFingerItemClass_AutoBind', ('description',)),
    '9b1d3a5931d2ff6b': ('UIKingRoadTaskItemClass_AutoBind', ('content',)),
    'ab417872f17dbe20': ('UIOperationDailyWishCardClass_AutoBind', ('taskContent',)),
    'b17d37164631e024': ('UIBagAcquireJumpTipClass_AutoBind', ('tip',)),
    'b6c8b154571c31e6': ('UIGameAtlasLordSubSkillItemClass_AutoBind', ('expandTip',)),
    'b72c2f0fdc2605fd': ('UIOperationUltimateActivityItemClass_AutoBind', ('description',)),
    'b9fc9d1dc96145af': ('UINewBieGuideKeywordItem4Class_AutoBind', ('description',)),
    'bbdb3ea439e614b6': ('UIOperationReferralExpertMainClass_AutoBind', ('requirements',)),
    'c251eb2750c11195': ('UICommonTips_Explain2Class_AutoBind', ('introduced', 'detailedIntroduction', 'detailedInfuseIntroduction')),
    'c4497b6197268693': ('UIGameAtlasLordItemClass_AutoBind', ('expandTip',)),
    'c50caf83e9458bb1': ('UINewbieGuideItem3Class_AutoBind', ('description',)),
    'c5a48d86ff60afb4': ('UICommonTips_Explain3Class_AutoBind', ('introduced',)),
    'd4e32d4474179f76': ('UICommonTips_Explain1Class_AutoBind', ('introduced',)),
    'e74f3e59ea29296b': ('UIOperationNewbieStartDetailsPanelClass_AutoBind', ('challengeInfo',)),
    'ee98057cf5f0556e': ('UIKingRordScoreConditionItemClass_AutoBind', ('description',)),
    'f0c69cd8778848ca': ('UINewBieGuideKeywordItem1Class_AutoBind', ('description',)),
    'f16d75845ec61a16': ('UIGameAtlasSelectEquipItemClass_AutoBind', ('description',)),
    'f509511b04015669': ('UINewBieGuideKeywordItem2Class_AutoBind', ('description',)),
    'f9907a8833ca359a': ('UIOperationGuiLaiTiaoZhanMailClass_AutoBind', ('description',)),
    'fa7212c4c155ce76': ('UIGameAtlasKeyWordInforClass_AutoBind', ('textContent',)),
    'ffd13339b9a772cb': ('UIKingRordConditionTaskItemClass_AutoBind', ('requirementsDescription',)),
    # --- расширение 14.10.2026: окна без покрытия (Связанные, инфо героя, подарки, викторина, путь к топу, новички, меню событий, подсказки предметов) ---
    '1749915de8ecbfef': ('UILordInforClass_AutoBind', ('name',)),  # «Просмотр шахматиста»: заголовок через SetName
    '64a984c3d7cb05b1': ('UIRelationInfoItemClass_AutoBind', ('content1', 'content2')),
    '12329e65911f92d7': ('UIRelationInfoItemStyle1Class_AutoBind', ('content1',)),
    'adac9c5032bfaa62': ('UIHeroLevelSourceItemClass_AutoBind', ('content',)),
    'aeab4157e9533b65': ('UIHeroStatisticsItemClass_AutoBind', ('content',)),
    'b35ace160b9ba4a4': ('UIHeroEntryDescriptionSmallItemClass_AutoBind', ('des',)),
    'cfba3b86b9ecf1b4': ('UIGameAtlasTitleDesItemClass_AutoBind', ('des',)),
    '80b31f035d391e31': ('UIHeroCollectionSingleTextItemClass_AutoBind', ('textContent',)),
    '96f67d0e87e2cf3c': ('UICollectionHistoryTipClass_AutoBind', ('content',)),
    'a6fa6106ada86734': ('UIGameAtlasSelectHistoryTipClass_AutoBind', ('content',)),
    '81ed72585addc8ea': ('UICommonRewardDetailTipClass_AutoBind', ('description',)),
    '61735e7db9c935cf': ('UICareerDetailTipClass_AutoBind', ('description',)),
    '2901bcce4d2580fc': ('UICommonPropDetailsAndQuantitySelectionClass_AutoBind', ('description',)),
    'ff95352af7484e98': ('UIAffixDetailItemClass_AutoBind', ('Description', 'Mission')),
    '9a01400ec77cef5c': ('UILordSkinEffectDesItemClass_AutoBind', ('text',)),
    'fc9aebc1189f5273': ('UINewbieKeywordDesItemClass_AutoBind', ('des',)),
    '67bace159a7685a7': ('UICommonGiftBagTipsClass_AutoBind', ('description',)),
    'adebf094a70c3526': ('UICommonWanXiangGiftBagTipsClass_AutoBind', ('description',)),
    'c13821672fd46395': ('UICommonSkinChessMergeBuyClass_AutoBind', ('descriptionContent',)),
    '2dc7a975d7212247': ('UIGiftMsgEditItemClass_AutoBind', ('content',)),
    '2be7555cb78a21ee': ('UIGiftCardClass_AutoBind', ('content',)),
    'f0b8906bff2de199': ('UIGiftStateMessageClass_AutoBind', ('content',)),
    '5c7895ca209ea078': ('UIGiftSwitchPlayerInfoClass_AutoBind', ('giftContent',)),
    '7e4c783a977a1d88': ('UIGiftRecordsRecieveCardPageClass_AutoBind', ('content',)),
    '6ceeaa8a218933d8': ('UIGiftFavorabilityShowPopClass_AutoBind', ('Txt_Des',)),
    'fac83ca54c3589d7': ('UIFrinedGiftFavorabilityShowPopClass_AutoBind', ('Txt_Des',)),
    '12369f1367f0a3e1': ('UIGiftSuccessfullyGiftedPopClass_AutoBind', ('content',)),
    'd201d03bf13d4cc9': ('UIGiftAskPreviewClass_AutoBind', ('content',)),
    '8e16601a90d27c48': ('UIGiftPropRecordDetailsClass_AutoBind', ('des',)),
    '2740e35eff01bf6c': ('UINoticeRoseClass_AutoBind', ('notice',)),
    'db02f4462ebe77f4': ('UIComomGiftOpenClass_AutoBind', ('titleTip', 'sunbTitleTip')),
    '881d0ce685c495d4': ('UIGiftShareBtnGroupClass_AutoBind', ('soloCurrencyAddTextNotice',)),
    '060710882e924f3a': ('UISelfWishTextItemClass_AutoBind', ('content',)),
    'e59050e5f151eb97': ('UIAnswerResultTitleItemClass_AutoBind', ('question', 'subtitle')),
    '76edeaaf10a6b3dd': ('UIAnswerOptionItemClass_AutoBind', ('contentTxt1', 'contentTxt2')),
    'ebe3e56f595c739d': ('UIQuestionItemClass_AutoBind', ('problemTxt1', 'problemTxt2')),
    '4ab766493785da7b': ('UIAnsweringSettlePopClass_AutoBind', ('content',)),
    '2068b1fbd6255409': ('UIAnsweringSettlePop2Class_AutoBind', ('resultNotice', 'evaluationOne', 'evaluationTwo')),
    '4785e89a84c4f228': ('UIAnsweringSettlePop3Class_AutoBind', ('notice',)),
    '3475d2bd3c7bc34f': ('UIAnsweringSettlePop4Class_AutoBind', ('noticeOne', 'noticeTwo')),
    '5c93a193bf91cb21': ('UIActiveAnswerQustionViewClass_AutoBind', ('endNotice',)),
    '829081666cba5854': ('UIOperationCommonAnsweringSettlePopClass_AutoBind', ('content',)),
    'b414dcd68a5c5b04': ('UIAskQuestionItemClass_AutoBind', ('content',)),
    'c08c3373fc2efcc1': ('UIOperationAnswering_startClass_AutoBind', ('Txt_Tips',)),
    'ce913a808626e3b2': ('UIOperationAnswering_finishClass_AutoBind', ('Txt_Tips',)),
    '85b3a8be686487cc': ('UIOperationAnswering_settlementClass_AutoBind', ('Txt_Tips02',)),
    '969f18cc2cff90de': ('UIKingRoadRankInfoItemClass_AutoBind', ('tierDescription',)),
    'c586cc186e112d0a': ('UISupernovaRewardKingRoadBigItemClass_AutoBind', ('rewardDescriptionText',)),
    'b60bf0852b4ef0b7': ('UISupernovaKingRoadLevelUpClass_AutoBind', ('contentTxt',)),
    '7f154a0b58def1f9': ('UISupernovaRewardKingRoadSmallItemClass_AutoBind', ('promptTxt1', 'promptTxt2', 'promptTxt3')),
    '0138fd646c5befd6': ('UINewbieGuideDialogTipStyle1Class_AutoBind', ('TxtContent',)),
    'b6362cc7f8d4fd75': ('UINewbieGuideDialogTipStyle2Class_AutoBind', ('TxtContent',)),
    'ee554626069ebd14': ('UINewbieGuideDialogTipStyle3Class_AutoBind', ('TxtContent',)),
    '39b312a0ea9abc12': ('UINewbieGuideDialogTipClass_AutoBind', ('DialogTxt',)),
    '10deea2fa2c380dd': ('UINewbieGuideCardsEffectClass_AutoBind', ('highlightDescriptionOne', 'highlightDescriptionTwo')),
    '61e696e2b9181250': ('WidgetNewbieGuideFingerItemArrow_UClass_AutoBind', ('description',)),
    'd00f9093dc533ea1': ('WidgetNewbieGuideFingerItemArrow_DClass_AutoBind', ('description',)),
    'e575cd9b7b728129': ('UITabOperationItemClass_AutoBind', ('tabContentTxt1', 'tabContentTxt2')),
    'e84f94273b1460d5': ('UIOperationCollectionItemClass_AutoBind', ('descriptionTextOne', 'descriptionTextTwo')),
    '91f373327752a1e0': ('UIOperationCollectionClass_AutoBind', ('Txt_Tips',)),
    'af83941515e91927': ('UIOperationInviteGiftsGroupClass_AutoBind', ('firstPurchaseDescriptionOne', 'firstPurchaseDescriptionTwo', 'investmentDescriptionOne', 'investmentDescriptionTwo', 'linkDescriptionOne', 'linkDescriptionTwo')),
    '77b9fc81c3470133': ('UIOperationMarketingMainClass_AutoBind', ('txActivitytIntroduce',)),
    'e86717b262d46080': ('UIOperationMarketingBtnClass_AutoBind', ('buttonText',)),
    '7f8e76041e0c4fb6': ('UICheckInTitleClass_AutoBind', ('activitySubtitle',)),
    'd4e723380337f58a': ('UIReturnCheckInPanelClass_AutoBind', ('subtitle',)),
    '194121676922c215': ('UIGameCenterDailyRewardEnterClass_AutoBind', ('description',)),
    '6e669239ab38e70b': ('UILordAttackEffectRightTopItemClass_AutoBind', ('description',)),
    '61d4bdb7fa29aa45': ('UICommonTipsAcquireJumpClass_AutoBind', ('propTip',)),
    'c13a898f95fe2ffd': ('UICommonTipsAcquireJumpBtnClass_AutoBind', ('tip',)),
    '6dda3f308cc24fc2': ('UICommonTips_InteractionBtnClass_AutoBind', ('propTip',)),
    '88edd7107b83d082': ('UICommonTips_SimpleText1Class_AutoBind', ('content',)),
    'bcec0992a7c360c0': ('UICommonTips_SimpleText3Class_AutoBind', ('bottomTextContent',)),
    '0f7a973d99e3540f': ('UICommonTips_TitleText2Class_AutoBind', ('bottomContent',)),
    'b1ea51f3446bfd41': ('UICommonTips_TitleDataList3Class_AutoBind', ('bottomText',)),
    'c8f06b310932b003': ('UICommonTips_TitleDataList1Class_AutoBind', ('description',)),
    'b8a1e29792990ca5': ('UICommonTipOnlyTextClass_AutoBind', ('promptContent',)),
    'f8171b2d5d0f1a5f': ('UICommonTipClass_AutoBind', ('content',)),
    'ed4f07ac2eacc8fe': ('UICommonTips_EquipGridTipsClass_AutoBind', ('content',)),
    'f55d1d1110e7a323': ('UICommonEquipTipsItem1Class_AutoBind', ('info',)),
    '18df47918a08d023': ('UICommonHelpTipsItemClass_AutoBind', ('Content',)),
    'bf9fa4b1b1eaa943': ('UICommonCouponItemTipClass_AutoBind', ('textDescription',)),
    '26c75cb4012fb706': ('ItemInfoGroupClass_AutoBind', ('getPath',)),
    '2edf6a0c3c595d9e': ('MergeItemInfoGroupClass_AutoBind', ('descriptionName', 'descriptionRequirements')),
    '02dfb833a7112cc1': ('UICommonLingTipsClass_AutoBind', ('content',)),
    # --- контроллеры с прямым присваиванием self.<поле>.text = ... / UIUtility:SetTextByContent(self.<поле>, ...) (см. DIRECT) ---
    '0fa9bbc9d82f71fb': ('UIActiveTaskItemBase', ('taskTitle', 'taskProgress')),
    '131d0f3b0c28c189': ('UIGameAtlasCardInfoLevelDesItemClass', ('noLevelTxt',)),
    '1a2363c3ea46b3b1': ('UICareerRecieveTitleViewPopClass', ('contentTxt',)),
    '1ee062f289f5ddc0': ('UIRelationInfoItemClass', ('curContent',)),
    '9783088cc81b5ffe': ('UINewBieGuideEnergyStepTextItemClass', ('txtText1', 'txtText2')),
    'a6bdf7338906ec81': ('UINewbieGuideItem2Class', ('txtText1', 'txtText2')),
    'd76888d448ad6da6': ('UIRelationInfoItemStyle1Class', ('content1',)),
    'ec60a062baa3fc70': ('UIActiveNewOperationClass', ('skillIntroduction', 'quotationsFromChessPlayers', 'chessPlayerIntroduction')),
    'd987653d5e907e8f': ('MergeItemInfoGroupClass', ('getWay',)),
    # --- расширение 14.10.2026 (v9): описательные поля героя, достижений, справки и подтверждений ---
    'a91d7cbc079c3bae': ('UITalentDescItemClass_AutoBind', ('content',)),
    '776b605a16845b08': ('UITalentDescItemStyle1Class_AutoBind', ('content',)),
    'e841e6b302ea9141': ('UIOperationSkillInfoItemClass_AutoBind', ('content',)),
    '91dfc3072f340ef5': ('UICardBPMissionContentOGClass_AutoBind', ('talentDescription',)),
    'b6bd4e6c2aee8d7e': ('UITRDesItemClass_AutoBind', ('description',)),
    'c88dc88fea911fbd': ('UITRTalentListClass_AutoBind', ('description',)),
    'c9256eb614227964': ('UITRTalentOutSideClass_AutoBind', ('description',)),
    '732c3d2b7f4984d9': ('UITRLordItemClass_AutoBind', ('description',)),
    '2106c6f018b84fc2': ('UITRThreadItemClass_AutoBind', ('description',)),
    'f21a1a3e08d7a60c': ('UITREffectListClass_AutoBind', ('description',)),
    'ae25f9c71181d6b5': ('UITREffectOutSideClass_AutoBind', ('description',)),
    '8d8e384bfdc31839': ('UITRThreadOutSideItemClass_AutoBind', ('description',)),
    '4e333b1e3c53fccc': ('UITRThreadOutSideClass_AutoBind', ('description',)),
    'e58108993589262b': ('UITROperationHeroListItemClass_AutoBind', ('description',)),
    'a2dbdc72166a0ba0': ('UITRLordListOutSideItemClass_AutoBind', ('description',)),
    '2c75c6435106ad31': ('UITRComposeClass_AutoBind', ('lineupDescription',)),
    'c8e7ae354aa38ac7': ('UIAchieveSelectAchievePageClass_AutoBind', ('description',)),
    '483b10cd60af387f': ('UIAchieveCompletionProgressItemClass_AutoBind', ('tierDescription',)),
    'beb26c52450fb3e0': ('UICommonAchieveTipsClass_AutoBind', ('described',)),
    '96acd16b8f0eca99': ('UIAchieveChessboardTipsClass_AutoBind', ('des',)),
    'ffb86dbd995cc04f': ('UIHelpContentItemClass_AutoBind', ('txtContent',)),
    '6a433e4f6da01cbd': ('UIHelpContentItem2Class_AutoBind', ('txtContent',)),
    '9cd04ce21b288f50': ('UIHelpContentSyle_LeftAlignClass_AutoBind', ('txtContent',)),
    'a61b8f72a7dd52c0': ('UIHelpContentSyle_CenterAlignClass_AutoBind', ('txtContent',)),
    '9eaf7dd9fd461beb': ('UIHelpContentSyle_RightAlignClass_AutoBind', ('txtContent',)),
    'f1cf89bbc3f37889': ('UIHelpContentSmartSyle_LeftAlignClass_AutoBind', ('txtContent',)),
    '0a75422b42b1dd99': ('UIHelpContentSmartSyle_CenterAlignClass_AutoBind', ('txtContent',)),
    '4924a220a7bb2cd2': ('UIHelpContentSmartSyle_RightAlignClass_AutoBind', ('txtContent',)),
    '3c596b344d9403a6': ('UICommonHelpSystemClass_AutoBind', ('content',)),
    'e94bfc4b409b8579': ('UICommonHelpSystemGroupClass_AutoBind', ('txtContent',)),
    '3b11ce4931e9a7fb': ('UICommonHelpSystemVariousStyle2Class_AutoBind', ('txtContent',)),
    'f8d64291eeebfd23': ('UICommonHelpSystemWithImageClass_AutoBind', ('description',)),
    '6d73052e4b39b6fc': ('UIHelpLayoutItemStyle_Smart1Class_AutoBind', ('txtDesc',)),
    '2b2964bea65ad80c': ('UICommonTxtItemClass_AutoBind', ('content',)),
    'b226a061a393c853': ('UICommonTxtItemSmallClass_AutoBind', ('content',)),
    '9ab44a93ab16c961': ('UICommonTextWithTitleTipClass_AutoBind', ('described',)),
    '9e7b8c63cd472f91': ('UICommonConfirmWithNormalClass_AutoBind', ('content',)),
    'a6905215e37c0427': ('UICommonConfirmWithNormalAndTextClass_AutoBind', ('content',)),
    '1dc144bdf03b9e0f': ('UICommonConfirmWithPriceClass_AutoBind', ('content',)),
    '40a34a6dabb4b59f': ('UICommonConfirmWithPropClass_AutoBind', ('content', 'Tips2', 'Tips3')),
    'c6f70b2855b0a19a': ('UICommonNativeMsgBoxStyle_MClass_AutoBind', ('content',)),
    'b96b50b93d2006cb': ('UIEmbattleDesSmallItemClass_AutoBind', ('description',)),
    'ad2a8d3e9bb518ec': ('UIEmbattleSmallLordItemClass_AutoBind', ('description',)),
    'dbd25926e3f354f4': ('UIembattleDesItemClass_AutoBind', ('description',)),
    # «Просмотр шахматиста»: описание справа пишется циклом `texts[i].text = ...` (см. LOOP_TEXTS)
    '6fc67a6c9b0fe5de': ('UILordInforClass', ('age', 'weight', 'birthday', 'identity', 'character1', 'character2')),
}
# Ресурсы второго вида: контроллер сам присваивает поле (`self.<поле>.text = выражение` или `UIUtility:SetTextByContent(self.<поле>, ...)`),
# setter AutoBind тут не участвует. Вызов помощника ставится после КАЖДОЙ такой строки поля (кроме пустых и самоссылочных),
# восстановление поля - в `vOnUninitialize` контроллера. Ключи DIRECT есть и в TARGETS.
DIRECT = frozenset({
    '0fa9bbc9d82f71fb', 'ec60a062baa3fc70', '1ee062f289f5ddc0', 'd76888d448ad6da6',
    '1a2363c3ea46b3b1', '9783088cc81b5ffe', 'a6bdf7338906ec81', '131d0f3b0c28c189',
    'd987653d5e907e8f', '6fc67a6c9b0fe5de',
})
# Подвид DIRECT: контроллер собирает поля в локальный список `texts` и присваивает циклом `texts[i].text = ...`;
# вызов помощника ставится после каждого присваивания, восстановление полей `self.<поле>` - в `vOnUninitialize`.
LOOP_TEXTS = frozenset({'6fc67a6c9b0fe5de'})
# Диагностические метки (только с --diag-marker): ресурс -> поле. У поля из WRAPPED метка показывает ещё и решение помощника,
# у остальных («·») — только то, что исправленный Lua выполнился.
DIAG_FIELDS = {
    'aeff176f093cf41d': 'title', 'e607e142cfd91df0': 'skillName',
    '64a984c3d7cb05b1': 'content1',
    '61d4bdb7fa29aa45': 'propTip',
    'e575cd9b7b728129': 'tabContentTxt1',
    '7f8e76041e0c4fb6': 'activitySubtitle',
    'ebe3e56f595c739d': 'problemTxt1',
    '2be7555cb78a21ee': 'content',
    '969f18cc2cff90de': 'tierDescription',
    'adac9c5032bfaa62': 'content',
    'b35ace160b9ba4a4': 'des',
    'c13a898f95fe2ffd': 'tip',
}
RESOURCE = re.compile(r'[0-9a-f]{16}')
SHA256 = re.compile(r'[0-9a-f]{64}')


class LuaPackageError(bp.PilotError):
    """Исходный Lua не совпал с проверенным или окружение не годится."""


# Общее покрытие (v12c). Помощник переноса определяется ОДИН раз в общем чанке - Lua-модуле UIPrefabClass (базовый класс всех UI-префабов:
# каждый AutoBind-модуль на верхнем уровне читает ClassLib.UIPrefabClass, значит этот чанк выполнен раньше любого UI) и выставляет
# _G._AceRuWrap / _G._AceRuWrapRestore. Остальные модули (COVERAGE) получают только короткий вызов после `self.<поле>.text = arg`;
# нет помощника в _G = штатный текст (fail-closed). TARGETS (инлайн-помощник в каждом чанке) остаются как есть.
COVERAGE_FILE = PROJECT / 'source/game-wordwrap/coverage-targets.json'
SHARED_MODULE = 'UIPrefabClass'
SHARED_EXPORT = ('-- ACE: help-wrap is defined once here; covered UI modules call it only when rawget(_G, "_AceRuWrap") is set\n'
                 'rawset(_G, "_AceRuWrap", _AceRuWrap)\n'
                 'rawset(_G, "_AceRuWrapRestore", _AceRuWrapRestore)\n')
COVER_CALL = 'if rawget(_G, "_AceRuWrap") then pcall(_G._AceRuWrap, self.{field}, arg) end'
COVER_RESTORE = '    if rawget(_G, "_AceRuWrapRestore") then pcall(_G._AceRuWrapRestore, self.{field}) end'


def load_coverage(path: Path = COVERAGE_FILE) -> tuple[str | None, dict[str, tuple[str, tuple[str, ...]]], dict[str, str]]:
    """(id общего чанка, {ресурс: (модуль, поля)}, {id: SHA оригинала}) из coverage-targets.json; файла нет - покрытия нет."""
    if not path.is_file():
        return None, {}, {}
    data = json.loads(path.read_text('utf-8'))
    if data.get('package') != PACKAGE:
        raise LuaPackageError('coverage-targets.json: не пакет 10')
    shared = data['shared']
    if not RESOURCE.fullmatch(shared['id_hex']) or shared['module'] != SHARED_MODULE or not SHA256.fullmatch(shared['sha256']):
        raise LuaPackageError('coverage-targets.json: неверная запись общего чанка')
    modules, pins = {}, {shared['id_hex']: shared['sha256']}
    for rid, row in data['modules'].items():
        if not RESOURCE.fullmatch(rid) or not SHA256.fullmatch(row['sha256']) or not row['fields'] or not re.fullmatch(r'\w+_AutoBind', row['module']):
            raise LuaPackageError(f'coverage-targets.json: неверная запись {rid}')
        if rid in TARGETS or rid == shared['id_hex']:
            raise LuaPackageError(f'coverage-targets.json: {rid} уже занят (TARGETS или общий чанк)')
        modules[rid] = (row['module'], tuple(row['fields']))
        pins[rid] = row['sha256']
    return shared['id_hex'], modules, pins


SHARED_ID, COVERAGE, COVERAGE_PINS = load_coverage()
HEADER_TARGETS = frozenset(header_layout.TARGETS)
ALL_IDS = frozenset(TARGETS) | frozenset(COVERAGE) | HEADER_TARGETS | ({SHARED_ID} if SHARED_ID else frozenset())
# Пины оригиналов покрытия вливаются в белый список контракта на время процесса (как BC7-пины); постоянная запись - в pilot_contract.py.
for _rid, _sha in COVERAGE_PINS.items():
    if pilot_contract.LUA_REPLACEMENT_ALLOWED[PACKAGE].setdefault(_rid, _sha) != _sha:
        raise LuaPackageError(f'{_rid}: пин покрытия расходится с pilot_contract.LUA_REPLACEMENT_ALLOWED')


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _line(text: str, pattern: str, what: str) -> 're.Match':
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise LuaPackageError(f'{what}: ожидалось ровно одно место, найдено {len(matches)}')
    return matches[0]


def patch_lua(original: bytes, rid: str, diag: bool, helper: str) -> tuple[bytes, list[dict]]:
    """Нагрузка ресурса: помощник в начале + вызовы после присваиваний. Доказывает обратимость (оригинал побайтно)."""
    module, fields = TARGETS[rid]
    if original.startswith(b'\xef\xbb\xbf'):
        raise LuaPackageError(f'{rid}: ресурс начинается с BOM')
    text = original.decode('utf-8')
    # Окончания строк в ресурсах бывают смешанными: вставки берут окончание своей строки, префикс - преобладающее.
    eol = '\r\n' if text.count('\r\n') * 2 >= text.count('\n') else '\n'
    declared = f'DeclareClass("{module}"' if rid in DIRECT else f'DeclarePrivateClass("{module}"'
    if declared not in text:
        raise LuaPackageError(f'{rid}: модуль {module} не найден')
    modified, edits = text, []
    wrap_call = {f: f'_AceRuWrap(self.{f}, arg)' for f in fields}
    diag_field = DIAG_FIELDS.get(rid) if diag else None

    def insert_after(source: str, anchor: re.Match, call: str, edit_id: str) -> str:
        edit = anchor.group(0) + anchor.group('indent') + call + anchor.group('eol')
        edits.append({'id': edit_id, 'original': anchor.group(0), 'replacement': edit})
        return source[:anchor.start()] + edit + source[anchor.end():]

    def inside_function(source: str, anchor: re.Match, rule: str) -> bool:
        header = source.rfind('\nfunction ', 0, anchor.start()) + 1
        return bool(re.match(rule, source[header:])) and '\nend' not in source[header:anchor.start()]

    if rid in DIRECT:
        if rid in LOOP_TEXTS:
            # Поля собираются в локальный список `texts`, текст пишется циклом `texts[i].text = ...`: вызов ставится после каждого такого присваивания.
            for field in fields:
                if len(re.findall(r'\bself\.' + re.escape(field) + r'\b', modified)) != 1:
                    raise LuaPackageError(f'{rid} {field}: поле должно встречаться в модуле ровно один раз (список texts)')
            list_anchor = _line(modified, r'^[ \t]*local texts[ \t]*=', f'{rid} список texts')
            loop_sites = []
            for anchor in re.finditer(r'^(?P<indent>[ \t]*)texts\[i\]\.text[ \t]*=(?!=)[ \t]*(?P<rhs>[^\r\n]*?)[ \t]*(?:--[^\r\n]*)?(?P<eol>\r?\n)', modified, re.M):
                rhs = anchor.group('rhs')
                if not rhs or 'texts[i]' in rhs or rhs.endswith((',', '..', ' and', ' or')) or rhs.count('(') != rhs.count(')'):
                    continue
                if anchor.start() < list_anchor.start() or not inside_function(modified, anchor, r'function \w+[:.]\w+\('):
                    raise LuaPackageError(f'{rid}: присваивание texts[i] вне функции модуля {module}')
                loop_sites.append(anchor)
            if not 1 <= len(loop_sites) <= 4:
                raise LuaPackageError(f'{rid}: присваиваний texts[i].text найдено {len(loop_sites)}, ожидалось 1-4')
            for number, anchor in enumerate(sorted(loop_sites, key=lambda a: a.start(), reverse=True)):
                modified = insert_after(modified, anchor, '_AceRuWrap(texts[i])', f'space-wrap-texts-loop-{len(loop_sites) - number}')
        for field in (() if rid in LOOP_TEXTS else fields):
            name = re.escape(field)
            patterns = (
                r'^(?P<indent>[ \t]*)self\.' + name + r'\.text[ \t]*=(?!=)[ \t]*(?P<rhs>[^\r\n]*?)[ \t]*(?:--[^\r\n]*)?(?P<eol>\r?\n)',
                r'^(?P<indent>[ \t]*)UIUtility:SetTextByContent\(self\.' + name + r', (?P<rhs>[^\r\n]*)\)[ \t]*(?:--[^\r\n]*)?(?P<eol>\r?\n)',
            )
            sites = []
            for pattern in patterns:
                for anchor in re.finditer(pattern, modified, re.M):
                    rhs = anchor.group('rhs').strip()
                    if rhs in ('""', "''", 'nil') or f'self.{field}' in rhs or rhs.count('(') != rhs.count(')') or rhs.endswith((',', '..', ' and', ' or')):
                        continue
                    if not inside_function(modified, anchor, r'function \w+[:.]\w+\('):
                        raise LuaPackageError(f'{rid} {field}: присваивание вне функции модуля {module}')
                    sites.append(anchor)
            if not sites:
                raise LuaPackageError(f'{rid} {field}: нет подходящего присваивания')
            if len(sites) > 12:
                raise LuaPackageError(f'{rid} {field}: слишком много присваиваний ({len(sites)})')
            for number, anchor in enumerate(sorted(sites, key=lambda a: a.start(), reverse=True)):
                modified = insert_after(modified, anchor, f'_AceRuWrap(self.{field})', f'space-wrap-{field}-{len(sites) - number}')
        hook, hook_name = r'^(?P<indent>)function ' + re.escape(module) + r':vOnUninitialize\([^)\r\n]*\)(?P<eol>\r?\n)', 'vOnUninitialize'
        restore_id = 'restore-own-text-fields-on-uninitialize'
    else:
        for field in fields + ((diag_field,) if diag_field and diag_field not in fields else ()):
            pattern = r'^(?P<indent>[ \t]*)self\.' + re.escape(field) + r'\.text = arg(?P<eol>\r?\n)'
            anchor = _line(modified, pattern, f'{rid} {field}')
            if not inside_function(modified, anchor, r'function ' + re.escape(module) + r':Set\w+\(arg\)\r?\n'):
                raise LuaPackageError(f'{rid} {field}: присваивание вне setter модуля {module}')
            if field in fields and field == diag_field:
                call = f'_AceRuMark(self.{field}, _AceRuWrap(self.{field}, arg))'
            elif field in fields:
                call = wrap_call[field]
            else:
                call = f'_AceRuMark(self.{field})'
            modified = insert_after(modified, anchor, call, ('diag-mark-' if field == diag_field else 'space-wrap-') + field)
        hook, hook_name = r'^(?P<indent>)function ' + re.escape(module) + r':vOnResourceUnLoaded\(\)(?P<eol>\r?\n)', 'vOnResourceUnLoaded'
        restore_id = 'restore-own-text-fields-on-native-unload'
    anchor = _line(modified, hook, f'{rid} {hook_name}')
    release = ''.join('    pcall(_AceRuWrapRestore, self.' + f + ')' + anchor.group('eol') for f in fields)
    edit = anchor.group(0) + release
    edits.append({'id': restore_id, 'original': anchor.group(0), 'replacement': edit})
    modified = modified[:anchor.start()] + edit + modified[anchor.end():]
    prefix = MARKER + eol + helper.replace('\n', eol) + eol
    # Обратная правка: убрать префикс и вставки -> побайтно исходный ресурс.
    payload = (prefix + modified).encode('utf-8')
    restored = payload.decode('utf-8')
    if not restored.startswith(prefix):
        raise LuaPackageError(f'{rid}: префикс помощника потерян')
    restored = restored[len(prefix):]
    for replacement in {e['replacement'] for e in edits}:
        same = [e for e in edits if e['replacement'] == replacement]
        if restored.count(replacement) != len(same) or len({e['original'] for e in same}) != 1:
            raise LuaPackageError(f'{rid}: правка {same[0]["id"]} не однозначна')
        restored = restored.replace(replacement, same[0]['original'])
    if restored.encode('utf-8') != original or not payload.startswith(pilot_contract.LUA_PAYLOAD_MARKER):
        raise LuaPackageError(f'{rid}: обратная правка не восстановила оригинал')
    return payload, [{'id': e['id']} for e in edits]


def _eol_of(text: str) -> str:
    return '\r\n' if text.count('\r\n') * 2 >= text.count('\n') else '\n'


def patch_shared(original: bytes, helper: str) -> bytes:
    """Общий чанк (UIPrefabClass): маркер + помощник (+ диагностический слой) + выставление в _G перед оригиналом. Доказывает обратимость."""
    if original.startswith(b'\xef\xbb\xbf'):
        raise LuaPackageError('Общий чанк начинается с BOM')
    text = original.decode('utf-8')
    if f'DeclareClass("{SHARED_MODULE}")' not in text:
        raise LuaPackageError(f'Общий чанк: модуль {SHARED_MODULE} не найден')
    eol = _eol_of(text)
    prefix = MARKER + eol + helper.replace('\n', eol) + eol + SHARED_EXPORT.replace('\n', eol)
    payload = (prefix + text).encode('utf-8')
    if not payload.startswith(pilot_contract.LUA_PAYLOAD_MARKER) or payload.decode('utf-8')[len(prefix):].encode('utf-8') != original:
        raise LuaPackageError('Общий чанк: обратная правка не восстановила оригинал')
    return payload


def patch_covered(original: bytes, rid: str, module: str, fields: tuple[str, ...]) -> tuple[bytes, list[dict]]:
    """Покрытый модуль: после `self.<поле>.text = arg` короткий вызов _G._AceRuWrap, в vOnResourceUnLoaded - _G._AceRuWrapRestore.

    Помощника в модуле нет. Каждое поле - ровно одно присваивание в setter `<модуль>:Set...(arg)`; обратная правка даёт оригинал побайтно."""
    if original.startswith(b'\xef\xbb\xbf'):
        raise LuaPackageError(f'{rid}: ресурс начинается с BOM')
    text = original.decode('utf-8')
    eol = _eol_of(text)
    if f'DeclarePrivateClass("{module}"' not in text:
        raise LuaPackageError(f'{rid}: модуль {module} не найден')
    modified, edits = text, []
    for field in fields:
        anchor = _line(modified, r'^(?P<indent>[ \t]*)self\.' + re.escape(field) + r'\.text = arg(?P<eol>\r?\n)', f'{rid} {field}')
        header = modified.rfind('\nfunction ', 0, anchor.start()) + 1
        if not re.match(r'function ' + re.escape(module) + r':Set\w+\(arg\)\r?\n', modified[header:]) or '\nend' in modified[header:anchor.start()]:
            raise LuaPackageError(f'{rid} {field}: присваивание вне setter модуля {module}')
        edit = anchor.group(0) + anchor.group('indent') + COVER_CALL.format(field=field) + anchor.group('eol')
        edits.append({'id': f'shared-wrap-{field}', 'original': anchor.group(0), 'replacement': edit})
        modified = modified[:anchor.start()] + edit + modified[anchor.end():]
    anchor = _line(modified, r'^(?P<indent>)function ' + re.escape(module) + r':vOnResourceUnLoaded\(\)(?P<eol>\r?\n)', f'{rid} vOnResourceUnLoaded')
    edit = anchor.group(0) + ''.join(COVER_RESTORE.format(field=f) + anchor.group('eol') for f in fields)
    edits.append({'id': 'shared-restore-on-native-unload', 'original': anchor.group(0), 'replacement': edit})
    modified = modified[:anchor.start()] + edit + modified[anchor.end():]
    prefix = MARKER + eol
    payload = (prefix + modified).encode('utf-8')
    restored = payload.decode('utf-8')[len(prefix):]
    for replacement in {e['replacement'] for e in edits}:
        same = [e for e in edits if e['replacement'] == replacement]
        if restored.count(replacement) != len(same) or len({e['original'] for e in same}) != 1:
            raise LuaPackageError(f'{rid}: правка {same[0]["id"]} не однозначна')
        restored = restored.replace(replacement, same[0]['original'])
    if restored.encode('utf-8') != original or not payload.startswith(pilot_contract.LUA_PAYLOAD_MARKER):
        raise LuaPackageError(f'{rid}: обратная правка не восстановила оригинал')
    return payload, [{'id': e['id']} for e in edits]


def helper_text(diag_log: bool = False) -> str:
    """Текст помощника для нагрузок. Обычная сборка - ru-space-wrap.lua как есть; --diag-log - он же плюс диагностический слой (шаблон с подстановками)."""
    base = HELPER.read_text('utf-8')
    if not diag_log:
        return base
    layer = DIAG_LAYER.read_text('utf-8')
    if DIAG_MODULE_PLACEHOLDER not in layer or DIAG_RESOURCE_PLACEHOLDER not in layer or 'local function _AceRuWrap(' not in layer:
        raise LuaPackageError('Диагностический слой без подстановок модуля/ресурса или без обёртки _AceRuWrap')
    return base.rstrip('\n') + '\n\n' + layer.rstrip('\n') + '\n'


def helper_sha_of(diag_log: bool = False) -> str:
    """SHA помощника для манифеста: обычная сборка - файл помощника побайтно (закреплён в game_lang_layer_build); диагностическая - шаблон base+слой."""
    if not diag_log:
        return sha_bytes(HELPER.read_bytes())
    return sha_bytes(helper_text(True).encode('utf-8'))


def read_resources(game: Path, diag: bool, diag_log: bool = False, only: frozenset | set | None = None) -> list[dict]:
    """Оригиналы пакета 10 из prime (SHA сверяются с закреплёнными) и новые нагрузки. only - подмножество id (для тестов); сборка берёт все."""
    helper = helper_text(diag_log)
    for name in ('_AceRuSpaceWrap', '_AceRuWrapRestore', '_AceRuWrap', '_AceRuMark'):
        if f'local function {name}(' not in helper:
            raise LuaPackageError(f'В помощнике нет {name}')
    if set(ALL_IDS) != set(pilot_contract.LUA_REPLACEMENT_ALLOWED[PACKAGE]) or not all(RESOURCE.fullmatch(r) for r in ALL_IDS):
        raise LuaPackageError('TARGETS/покрытие расходится с белым списком pilot_contract.LUA_REPLACEMENT_ALLOWED')
    if COVERAGE and not SHARED_ID:
        raise LuaPackageError('Есть покрытые модули, но нет общего чанка')
    resources = []
    for rid in sorted(ALL_IDS if only is None else ALL_IDS & set(only)):
        original, path = bp.read_original(game, PACKAGE, rid)
        if sha_bytes(original) != pilot_contract.LUA_REPLACEMENT_ALLOWED[PACKAGE][rid]:
            raise LuaPackageError(f'{rid}: оригинал в игре отличается от закреплённого (другая сборка?)')
        if rid in HEADER_TARGETS:
            try:
                payload, edits = header_layout.patch_resource(original, rid)
            except header_layout.HeaderLayoutError as error:
                raise LuaPackageError(str(error)) from error
            module, fields = header_layout.TARGETS[rid]['module'], ()
        elif rid in TARGETS:
            own_helper = helper.replace(DIAG_MODULE_PLACEHOLDER, TARGETS[rid][0]).replace(DIAG_RESOURCE_PLACEHOLDER, rid) if diag_log else helper
            payload, edits = patch_lua(original, rid, diag, own_helper)
            module, fields = TARGETS[rid]
        elif rid == SHARED_ID:
            own_helper = helper.replace(DIAG_MODULE_PLACEHOLDER, SHARED_MODULE + '-shared').replace(DIAG_RESOURCE_PLACEHOLDER, rid) if diag_log else helper
            payload, edits = patch_shared(original, own_helper), [{'id': 'shared-helper-prefix'}]
            module, fields = SHARED_MODULE, ()
        else:
            module, fields = COVERAGE[rid]
            payload, edits = patch_covered(original, rid, module, fields)
        resources.append({'package': PACKAGE, 'id_hex': rid, 'kind': 'lua-script', 'original': original, 'original_sha256': sha_bytes(original),
                          'items': [], 'data_file': path.name, 'alias': f'/Pilot/{rid}.lua', 'payload': payload, 'sha256': sha_bytes(payload),
                          'module': module, 'fields': list(fields), 'edits': edits})
    return resources


def finalize(output: Path, manifest: dict, resources: list[dict], diag: bool, seconds: float, diag_log: bool = False) -> None:
    helper_sha = helper_sha_of(diag_log)
    manifest['lua_replacements'] = [{'package': r['package'], 'id_hex': r['id_hex'], 'kind': 'lua-script', 'original_sha256': r['original_sha256'],
                                     'sha256': r['sha256'], 'bytes': len(r['payload']), 'original_bytes': len(r['original'])} for r in resources]
    manifest['lua_wrap'] = {'helper': str(HELPER.relative_to(PROJECT)), 'helper_sha256': helper_sha, 'diagnostic_marker': diag,
                            'diag_log': diag_log, 'diag_layer': str(DIAG_LAYER.relative_to(PROJECT)) if diag_log else None,
                            'base_helper_sha256': sha_bytes(HELPER.read_bytes()),
                            'diagnostic_fields': DIAG_FIELDS if diag else {},
                            'shared': {'id_hex': SHARED_ID, 'module': SHARED_MODULE, 'exports': ['_AceRuWrap', '_AceRuWrapRestore'], 'call': COVER_CALL, 'restore': COVER_RESTORE.strip()} if SHARED_ID else None,
                            'coverage': {'file': str(COVERAGE_FILE.relative_to(PROJECT)), 'file_sha256': sha_bytes(COVERAGE_FILE.read_bytes()), 'modules': len(COVERAGE),
                                         'fields': sum(len(v[1]) for v in COVERAGE.values()), 'inline_targets': len(TARGETS)} if SHARED_ID else None,
                            'targets': [{'id_hex': r['id_hex'], 'module': r['module'], 'fields': r['fields'], 'edits': [e['id'] for e in r['edits']]} for r in resources]}
    manifest['content_sha256'] = helper_sha
    header_resources = [r for r in resources if r['id_hex'] in HEADER_TARGETS]
    if header_resources:
        manifest['header_layout'] = {
            'helper': str(header_layout.HELPER.relative_to(PROJECT)),
            'helper_sha256': sha_bytes(header_layout.HELPER.read_bytes()),
            'metadata': str(header_layout.METADATA.relative_to(PROJECT)),
            'metadata_sha256': sha_bytes(header_layout.METADATA.read_bytes()),
            'targets': [{'id_hex': r['id_hex'], 'module': r['module']} for r in header_resources],
            'trigger': 'native-title-or-left-control-update',
            'runtime_guard': 'russian-locale-cyrillic-title-common-horizontal-layout-group-existing-layout-element',
            'native_prefab_hierarchy_verified': False,
            'in_game_verified': False,
        }
    manifest['limitations'] = [
        'Игра и лаунчер не запускались: загрузит ли игра/ACE Lua из override пакета 10 и сработает ли подгонка на реальных окнах, не проверено',
        'Перенос работает только при locale-guard: Localize.GetString("TitleSetting") содержит кириллицу (v12; «Настройки» и «Опции» проходят), и только для кириллицы в поле; иначе поле остаётся как есть',
        'Слово шире строки режется внутри себя (до 40 замеров на слово); несбалансированная известная разметка, нехватка высоты фиксированного поля (наш текст выше штатного более чем на 1,5 кегля) или бюджет измерений возвращают исходный текст поля',
        'Диагностика отказов: строки «модуль поле причина» в %TEMP%\\ace-ru-wrap.log (до 200 за сессию); работает, только если io/os доступны в Lua игры',
        'Общее покрытие: помощник стоит в чанке UIPrefabClass и выставляет _G._AceRuWrap; порядок загрузки (UIPrefabClass раньше любого UI-модуля) выведен из кода, в игре не проверен; в журнале ace-ru-wrap.log модуль вызывающего у таких полей показан как [C] (вызов через pcall)',
        'Явные LF внутри поля с HorizontalWrapMode.Overflow и сохранение <c>/<size> между строками в Timi mesh не проверены в Unity',
        'Общая русская шапка: только существующий общий HorizontalLayoutGroup и LayoutElement, preferredWidth после штатного rebuild, не более 16 соседей и 4 уровней родителей; нет подходящей раскладки — штатная геометрия. Иерархия настоящего prefab и итоговый зазор в игре не проверены',
    ] + (['ДИАГНОСТИЧЕСКАЯ сборка: к заголовку окна подсказок настроек и к имени умения в карточке героя дописывается «·»; для релиза пересобрать без --diag-marker'] if diag else []) \
      + (['ДИАГНОСТИЧЕСКАЯ сборка журнала (--diag-log): слой ru-space-wrap-diag.lua пишет %TEMP%\\ace-ru-diag.log (chunk/call/err, только числа и коды); для релиза пересобрать без --diag-log. Игра не запускалась'] if diag_log else [])
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    source = output / 'pilot-content.used.json'
    if source.exists():
        source.replace(output / 'lua-wrap-input.json')
    lines = ['# Отчёт Lua-носителя пакета 10', '',
             f'Версия игры: {manifest["game_version"]}; пакет: {PACKAGE}; собрано: {manifest["built_at"]}; время сборки: {seconds:.0f} с', '',
             'Статус: кандидат собран и проверен нативным SDK офлайн, в игру НЕ установлен.', '',
             f'Диагностический маркер: {"ДА" if diag else "нет"}; диагностический журнал (--diag-log): {"ДА" if diag_log else "нет"}; помощник {manifest["lua_wrap"]["helper"]} sha256 {helper_sha}', '',
             '## Ресурсы', '', '| ресурс | модуль | поля | байт было -> стало | sha256 оригинала | sha256 нагрузки |', '|---|---|---|---|---|---|']
    lines += [f'| {r["id_hex"]} | {r["module"]} | {", ".join(r["fields"])} | {len(r["original"])} -> {len(r["payload"])} | {r["original_sha256"]} | {r["sha256"]} |'
              for r in resources if r['id_hex'] not in COVERAGE]
    if COVERAGE:
        covered = [r for r in resources if r['id_hex'] in COVERAGE]
        lines += ['', f'Общее покрытие: {len(covered)} модулей, {sum(len(r["fields"]) for r in covered)} полей получили только короткий вызов _G._AceRuWrap '
                      f'(помощник один раз в общем чанке {SHARED_ID} / {SHARED_MODULE}); список - manifest.lua_wrap.targets.']
    reads = manifest['native_verification']['reads']
    lines += ['', '## Проверки', '', f'- нативных чтений совпало: {sum(1 for x in reads if x["matches"])}/{len(reads)}',
              '- каждая нагрузка: префикс помощника и вставки убираются обратно в оригинал побайтно; оригиналы совпали с закреплёнными SHA',
              f'- GlobalIndexPrime.data не менялся: {manifest["prime_global_index_sha256"]}', '', '## Не проверено', '']
    lines += [f'- {text}' for text in manifest['limitations']]
    (output / 'REPORT.md').write_text('\n'.join(lines) + '\n', 'utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-root', type=Path, default=None, help='По умолчанию установка, которую использует лаунчер')
    parser.add_argument('--output', type=Path, default=None, help='build/lua-wrap-<дата>-<id>')
    parser.add_argument('--diag-marker', action='store_true', help='Диагностическая сборка: метка «·» на заголовке окна настроек')
    parser.add_argument('--diag-log', action='store_true', help='Диагностическая сборка журнала: слой ru-space-wrap-diag.lua пишет %%TEMP%%\\ace-ru-diag.log (не путать с --diag-marker)')
    parser.add_argument('--payload-dir', type=Path, default=None, help='Записать готовые Lua-нагрузки (для проверки синтаксиса); без упаковки если с --check')
    parser.add_argument('--check', action='store_true', help='Только собрать и проверить нагрузки, пакет не упаковывать')
    parser.add_argument('--keep-lab', action='store_true')
    args = parser.parse_args(argv)
    started = time.time()
    try:
        game = wxq_install.require_install_structure(args.game_root if args.game_root else wxq_install.detect_active_install(), full=True)
        output = (args.output or PROJECT / 'build' / f'lua-wrap-{datetime.date.today().isoformat()}-{uuid.uuid4().hex[:6]}').resolve()
        if output.parent != (PROJECT / 'build').resolve() or not output.name.startswith('lua-wrap-'):
            print('Выход должен быть build/lua-wrap-<имя> внутри проекта', file=sys.stderr)
            return 2
        if output.is_relative_to(game):
            raise LuaPackageError('Выход не может лежать внутри игры')
        if (output / 'manifest.json').exists() or (output / 'packages').exists():
            raise LuaPackageError(f'Каталог уже содержит сборку: {output}')
        resources = read_resources(game, args.diag_marker, args.diag_log)
        if args.payload_dir:
            args.payload_dir.mkdir(parents=True, exist_ok=True)
            for r in resources:
                (args.payload_dir / f'{r["id_hex"]}.lua').write_bytes(r['payload'])
                (args.payload_dir / f'{r["id_hex"]}.original.lua').write_bytes(r['original'])
        print(json.dumps({'resources': len(resources), 'fields': sum(len(r['fields']) for r in resources), 'payload_bytes': sum(len(r['payload']) for r in resources),
                          'diag_marker': args.diag_marker, 'diag_log': args.diag_log}), flush=True)
        if args.check:
            return 0
        output.mkdir(parents=True, exist_ok=True)
        stub = output.with_name(output.name + '.input-stub.json')
        stub.write_text(json.dumps({'helper': str(HELPER), 'sha256': helper_sha_of(args.diag_log), 'targets': sorted(ALL_IDS), 'diag_marker': args.diag_marker, 'diag_log': args.diag_log}) + '\n', 'utf-8')
        try:
            manifest = bc.build(game, resources, output, stub, args.keep_lab, package=PACKAGE)
        finally:
            stub.unlink(missing_ok=True)
        finalize(output, manifest, resources, args.diag_marker, time.time() - started, args.diag_log)
    except bp.PilotError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'output': str(output), 'resources': len(resources), 'diag_marker': args.diag_marker, 'diag_log': args.diag_log, 'installed': False,
                      'native_reads_match': all(r['matches'] for r in manifest['native_verification']['reads']), 'seconds': round(time.time() - started)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
