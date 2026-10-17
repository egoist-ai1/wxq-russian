"""Контракт кандидата, описанный самим манифестом (пилот и последующие носители).

Старый контракт жёстко знал шесть файлов пакетов 1 и 10 сборки 1.3.0.4. Здесь набор
пакетов и файлов берётся из манифеста, а совпадение с установкой проверяется по данным
самой установки: версия `<pkg>.info`, `pkgHash` prime-пакета из QtsVFS.log, override-заголовок,
SHA prime-файлов и индекса. Ничего не пишет и не запускает.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

STATUS = 'offline-native-pilot-candidate-not-installed'
CONTRACT = 'wxq-pilot-carrier/v1'
SCHEMA_VERSION = 3
KINDS = ('global-localize', 'lang', 'rgba32-texture', 'bc7-texture', 'raw-replacement', 'lua-script', 'pb-table-text', 'unity-text')
# Текстовые правки таблиц пакета 1 (kind=pb-table-text) и Unity-файлов пакета 19 (kind=unity-text): оригиналы - только из белого списка пинов,
# правки перечислены в manifest['table_text_edits'] / manifest['unity_text_edits'] и ПОВТОРНО доказываются кодеком при проверке кандидата.
TEXT_KINDS = {'pb-table-text': ('1', 'table_text_edits'), 'unity-text': ('19', 'unity_text_edits')}
TEXT_RESOURCE_PINS_PATH = PROJECT / 'localization/profiles/text-resource-pins-1.5.0.1.json'
TEXT_RESOURCE_PINS_SHA256 = '2ed29187f0f7da65833a6013220eb7f8c2f5a0a66869dac88253c4f4fedf02b0'  # SHA-256 файла пинов (scripts/build_text_resource_pins.py)
# Ресурсы, которые можно заменить целиком готовыми байтами (правила переноса строк, Unity TextAsset пакета 1).
# Явный белый список: пакет -> {id: SHA-256 оригинала в prime}. Любой другой id как raw-replacement отклоняется.
RAW_REPLACEMENT_ALLOWED = {
    '1': {
        '1a0bdd5539b4d8e7': 'f9f54302ba7e8e7719a554cbb327c2c419927ae90aa84f5dc63e4a54870dd4e5',  # LineBreakingFollowingCharacters
        '9c52088bfb999c79': 'ac8fda88617abdc8939e0bb5fef6cf1148ad6be8973515312d86d9ceb53d6de1',  # LineBreakingLeadingCharacters
    },
}
# Lua-ресурсы пакета 10, которые можно заменить подготовленными патчами переноса по пробелам (kind=lua-script).
# Явный белый список: пакет -> {id: SHA-256 оригинала в prime}; пины сняты с WXQ(1) 1.5.0.1. Любой другой id отклоняется.
LUA_REPLACEMENT_ALLOWED = {
    '10': {
        'e607e142cfd91df0': '54e9b0083f9e5f913ab41dec70836f13a38f7db2e55474563e35ace58bec04bf',  # UICardDetailHeroSkill_AutoBind
        'aeff176f093cf41d': 'dec7f4e956525c2923406638fa31fcacdae8479514afd4bd6e85f8a11d912697',  # UICommonMSGBoxSettingsitem_AutoBind
        '99527bd5e8d17767': '72bb3df4867cf6b0496fdf9619c1c3c0727bfc1da7150373993abd61ba54fe63',  # UICardDetailSkillDesItem_AutoBind
        '1edf1b6871d8dc7a': '7ab19f613878090c2a343a336a6d9401e1f3b7d2bfa97643a3afcd064bc57f0a',  # UICardSkillDescriptionItem_AutoBind
        '3b4c55d975d49ceb': '645b32248fee66ef9102c8ae31de07c7457ec37808a065510f36403f5dde4f8b',  # UIHeroSkillDescription_AutoBind
        '38340509a07bbcbb': '342b6949a99492ae5766eb68433b15b355296b4379420f8bb3e886e950513337',  # UIHeroEnrtyDescriptionItem_AutoBind
        '50c290acc2a3b19e': 'a30b8340ed4c4478d2a8805551bce22ff04c9e94a0aee3fb21349fcfd5ced7e2',  # UICardExplanationItem_AutoBind
        '6fb7291c59f144ae': '68ded0815bf2ee0a525a9c6de374c43b8907fa48c667008709a5f243ec05fc33',  # UICardInfoLevelDesItem_AutoBind
        'c3dcb26ae530e158': 'ebffdfce921de885e375a964c8481232c3f68b63b59a45796f1a0e603c36bae9',  # UIGameAtlasCardInfoLevelDesItem_AutoBind
        '17e2f8afb42b56dd': '652a5fd5e11bf4edff53bec8c6fcdf0ae4e57fdf23f6af5157beda2975e0cf21',  # UIHeroSimpleDetailTips_AutoBind
        '3d24ad9cb9eabd8e': '4e0bbc88890b7ff6c7b8ae21eead9c2341f1d95d339a5980f23eabb2d98c76a5',  # UIGameAtlasSingleDesItem_AutoBind
        '2a2654f5fff909c7': '9eb4ff6da1431c64d50a3c7895bb6d041ba718dc6bbf8538bb7aec40b07060be',  # UIGameAtlasHeroSkillItem_AutoBind
        '13d839f568c430c7': '5f967ae6691cf263f5d0b4668568c74a9327c13bc7936940fa7efd8d7aa769e4',  # UIHeroCollectionSkillItem_AutoBind
        'aedf6ed2bd9f27e0': '686598778ea2a98cf947e4e9039aba999ddd33ff284b1fedaabee8f307f2f9f9',  # UICardTipsLordSkillEffectItem_AutoBind
        'c15e3fb56b8b9247': 'a2a7c76ac058c14f7c0239b6b83358244796ce9677158049ec61aecef2112b73',  # UICardTipsTextContentItem_AutoBind
        'a5b5ad814db94733': 'a74613a1736f1de6ecdc9f7121f4cc2d962416e878cd1fe0ba509fd8dbb727fc',  # UITipsCardsItem_AutoBind
        'd2f6d143fa8bdfb1': 'f06b4edd1fb49d7a5b945e7f3e49e1000a723cc2f21c4b91f5a8e7d8f2aec4d7',  # UICardDescForPandora_AutoBind
        '27bd54216c836e13': '8fb82df88dd65c48245d4e609e446b975e3f558751ea053f2b01b21c1d0b51e7',  # UIPreviewEffectDetailsItem_AutoBind
        'a2f4ecf20a9830a0': 'c937a0e936a388a32ed0b824474e36ad1e011e6a7ba4ec70cb59105c1d0241a3',  # UIPreviewEffectNormalDetailsItem_AutoBind
        '34ce6be147969f53': '44bb556b6cf111480f5ef15132b05f6b8ddf35fb6286f805c7bb7883f38e9e84',  # UIEmbattleLordSkillDescItem_AutoBind
        '4e54e09e1dec2d2c': '060e67f483210f676c367a771a7d82522a465229622b6f9545c01f95a4e5d428',  # UILordSkillDescItem_AutoBind
        '24a5bdab872c8d59': '769a657478371fd534641be610aee84af54ba105b7612a6fbd15161522a575f8',  # UILordSkillInfoItem_AutoBind
        '9f0a1c328fcf7760': '519a2153d2b328ad08c201dac07fd29e7afb13c5d890e21d79c37e0ad48a3802',  # UICardLevelUpEffectItem_AutoBind
        '0138613d8915af8f': '0c65ad17dcbb55081df9c16a365277cb7670e48c5baa54f3843a8bb18c4d303b',  # UINewBieGuideEnergyStepTextItem_AutoBind
        '03e7760ff85fee7d': 'bfe6ec5ebbe593e2d6cc92f9f47f386b6025f48d646f967ed693e5a1ce130a0b',  # UIFriendAuthorizationPop_AutoBind
        '1432abb117a1d103': '9fb7b0b61b782c80f008a9523efd6ffe613095ff2f3f85f6f87b5e42ee514e4c',  # UINewbieGuideTipCenter2_AutoBind
        '14dec121f99f3c13': 'dc0ecb7e28d3b6015892ed5df6fe644a69df29d420cb68206656b7b1ec4255ca',  # UIOperationTeamInvite_AutoBind
        '14e7c1708f5def42': '02aa326e6026c31784f65730430a07bdde22fc200e86267104ee3fcb096c622d',  # UIBagDetail_AutoBind
        '18fa377c5b87a9ee': '975248a623a2adebe15cfee4e49115f82d7c4983ef30f7b3a8577f3d7550a0c7',  # UIOperationGuiLaiTiaoZhanSlapPage_AutoBind
        '1d64e5a6b3a66d14': '6befd4d8aada1dc6812aa24d1e223879410ae169aa73e16e55e86b92d279d38b',  # UIGameAtlasFlowItem_AutoBind
        '392a9662d3a36d50': 'b15b0baf2f418e274f049ec835bcfe37ecfe0748dbbdf6fe98b82e033e20cead',  # UINewbieGuideItem2_AutoBind
        '39422ecbfe2e5839': '5499280e122177a98b09b308c592b971b2a88213d3d0a2bfefcba568e9f04dd4',  # UIOperationGuiLaiTiaoZhanHotLineup2_AutoBind
        '3f6e43f0668755c2': '25181ab17fae5da81a2b9273297e1cd36195e4ad825b4717338e3b1d587b87d0',  # UIBPTaskItem_AutoBind
        '450b988136ab07b3': '35abd9b9fafe33c9c9668c0e86fdd1f8ef84f8ddac89b036916a446851abd5cb',  # UIOperationChessPlayerThemePage3_AutoBind
        '4aa6c8da11cb1608': '947a24f9289235a8b644bfde377678888ac20c80042c7ba71b34b6399660ae52',  # UINewBieGuideKeywordItem3_AutoBind
        '5205c92e6c7933fc': '690d50bec50c854e80f63c86ae12a402b3c9f2ed81c0103dfe57673fe0deb28c',  # UIOmniCommissionNewPlayerGuide_AutoBind
        '5548fa4e1a9a179c': 'd84b0b56ea66ab5c8c8a8f9a6523c21606c04e6e27603ced176a7b78a83714e8',  # UIOperationSpacialWishCard_AutoBind
        '55a86aea2f2f19b8': '7d13a18aba8807ad9cf9f56ed5075dabb5d3dfa2be948a042b12020c05767998',  # UIAchieveListPage_AutoBind
        '5737770b9be55ab9': 'b4cb10cac2a7ab36b78d17d528c9cd10b5a5e4c61bac116cfbebd08a1a858165',  # UIAchieveSettleItem_AutoBind
        '5e99f2b9f0963c0b': 'f9e47617a9dfdf090ecbfbc7f8cd032020ab2e6f341f1c6795270243b992d315',  # UIOperationServerLaunchProgressAwardBigItem_AutoBind
        '5f3ad2b974bac90b': '873d7ac8e51602435b090582f9c0af8ea3163e6f47578fa2483a32e92b03ab09',  # UIOperationServerLaunchProgressMainPage_AutoBind
        '60cc4255b843c5e7': '5cf8ebea9a416ee25c6e99b0f9b696bddd31cc0bf85f5f4943344d77c79b9028',  # UIFriendItemStyle6_AutoBind
        '65f2752499035c96': '6654bf8154cae11f456bbfd30308e6eeb6dd8f7f26a1935cfc2cebf06db04dea',  # UINewbieGuideItem4_AutoBind
        '67201165cf99d8d7': '23f2e59d7ab5a68f28345db17d71ea7d3da624e095f50775d4188130758c7b62',  # UIAchievePackageTips_AutoBind
        '69570ba346920851': '0338fddd48a32d4754ecb6d4a8c147be9b115c9f7805bade6219cffc68bffd84',  # UIFriendGiftChoiceItem_AutoBind
        '6cc65a1022de08a2': 'ddf1c6296feb85c6d5c5fa268c742dc6e5337f532882c22f100a47dd41862ec1',  # UIAchieveRecieveSingleAchieveItem_AutoBind
        '739e32112b5b40a3': '961ecb4cbe383257b8f7b79f6794ec85186c667e5b7d15e2495823575a8204c7',  # UIPiggyBankTaskRewardItem_AutoBind
        '7cc621c296da0290': '3b1f572c1325ffcbfb88d8879ddec1bbd531f843f43caf96267a0a535f055879',  # UINoticePanel_AutoBind
        '8ccbf5a07eafa0b2': '94ed16d7ec7546935e0be86ef131552a105bbfe63ef277d067ef7070c535e2d9',  # UIGameAtlasOSHItem_AutoBind
        '90ac9cb9560b114b': '4adaf96e120178efa322cf57eea28e4389ce28bd5795b0fb09c56c82b13b5677',  # UIMailDetails_AutoBind
        '938e29efdf873b9d': 'f873948f75e4f8983919ba64a3c1b34a24840308dd808b5041d84cd75d0d3582',  # UINewBieGuideEnergyCustomizationItem_AutoBind
        '977975966686d5a4': 'fe188d68a87d08f591cafd58997c3ad62c70f694989cfe1b8245666e20e9c892',  # UIIntimateRewardItem_AutoBind
        '9a5efe219d464e13': '02bb60b6c03d1d27d848073c23a97401ad789a63d5f23dfd373b4f5b17ff1d25',  # UINewbieGuideFingerItem_AutoBind
        '9b1d3a5931d2ff6b': '733a360990c61e8d6b0360b4e44036ecefebb98ffb8503a8e1c3cdeabfbb603f',  # UIKingRoadTaskItem_AutoBind
        'ab417872f17dbe20': 'a6c67968c1d663bf9123cdb98303a6333b1401c4a54049eee9c14f50b5f4d971',  # UIOperationDailyWishCard_AutoBind
        'b17d37164631e024': '38ff75940d314eb0d4cb29ad7f93bae4de50fc0b69e69accd0e219e5e9cbe72d',  # UIBagAcquireJumpTip_AutoBind
        'b6c8b154571c31e6': '1f127559d016f3d8e41c6f95f4f40986b752bf3af318163fea782f0ddc46f5c2',  # UIGameAtlasLordSubSkillItem_AutoBind
        'b72c2f0fdc2605fd': 'c3824d338a59045aecb922780eff687d7b8a30bc7899e89f63df62b355018115',  # UIOperationUltimateActivityItem_AutoBind
        'b9fc9d1dc96145af': 'a5a53bdf3abe2e043def370af37a1fb6da69ba19aac350466da215da8bf717c5',  # UINewBieGuideKeywordItem4_AutoBind
        'bbdb3ea439e614b6': '4bd8275f86b308de860ab3970e1d1c1e1241b320785b9155e3e15a01c4306f28',  # UIOperationReferralExpertMain_AutoBind
        'c251eb2750c11195': 'adfc8544aafc715b5e6927ba100ec5b5af1d0e4ccd29e34a99ac38baa78e8c50',  # UICommonTips_Explain2_AutoBind
        'c4497b6197268693': '7d5aa317d11d2063d4bf6df3de196a80148783e4e3161b14e3ee1619bf44c27e',  # UIGameAtlasLordItem_AutoBind
        'c50caf83e9458bb1': '646af4c6392d9e5d16c312304b51c8c392c929ba0b93dd0a6b2d2118f13222ea',  # UINewbieGuideItem3_AutoBind
        'c5a48d86ff60afb4': '5a2e3a8ac201c255e1b1f8a1ce9aa113391599aff1d1f3dfc6ad71d6e54221d2',  # UICommonTips_Explain3_AutoBind
        'd4e32d4474179f76': '55fb619748cab553c894dec9ffc2355af81bbfd0f7c21c9a07665f711a165936',  # UICommonTips_Explain1_AutoBind
        'e74f3e59ea29296b': '37d5b3b94c2002feb33d1939ee2a604198c4bf20678c2c6a92756745232f7688',  # UIOperationNewbieStartDetailsPanel_AutoBind
        'ee98057cf5f0556e': 'af176f067225529eced6501dafc20b279b5036d028fdff2f55f91f5d48ac0956',  # UIKingRordScoreConditionItem_AutoBind
        'f0c69cd8778848ca': '765ab1036f4b00938d7ca4c194ef3c1fe206ecd7b666669002fb061d0aa0ce7a',  # UINewBieGuideKeywordItem1_AutoBind
        'f16d75845ec61a16': 'b48e30d2d3a43fb534ab6e8b7fc4e7f7bdfcbb18b0aa2469b76d2cb4a9933208',  # UIGameAtlasSelectEquipItem_AutoBind
        'f509511b04015669': '7616650d1ad15989963e0e21ca2293195ba6bb29485e5c6a32cab00935c1e626',  # UINewBieGuideKeywordItem2_AutoBind
        'f9907a8833ca359a': '52d5f1840486ce7bd4a37f7384623170085ad41d8e48643294804a3cdece78d5',  # UIOperationGuiLaiTiaoZhanMail_AutoBind
        'fa7212c4c155ce76': 'b732eaa5cb7eeea1f0cfbc6d14d89172b5c2c1147e43784f57dbd6c11a53ee6a',  # UIGameAtlasKeyWordInfor_AutoBind
        'ffd13339b9a772cb': '0529904a646583c3fd625c78dc46a382e606c15caf64169bf1d289a31eff6ac5',  # UIKingRordConditionTaskItem_AutoBind
        '1749915de8ecbfef': '6f9272e02ee9229f5bd56e9f3ed4895d68a8d08737d7b3020c3090e5b714718a',  # UILordInforClass_AutoBind
        '64a984c3d7cb05b1': 'a1d504626085abf37fda8cb2343f36f716b6bf15f2af9109e5903b62c91e57d1',  # UIRelationInfoItemClass_AutoBind
        '12329e65911f92d7': '021de54d985b1f3bfa9d6c6f12903deab98d1be6ad7ed397b609fe8da0a0ac37',  # UIRelationInfoItemStyle1Class_AutoBind
        'adac9c5032bfaa62': 'ee20307c027bc16b66f94caa15a25f7425eb23d408eb71300c19559707c4e8fe',  # UIHeroLevelSourceItemClass_AutoBind
        'aeab4157e9533b65': '0cbd60b4b463ae78c6091fc8af8abeeb62855e0d75c4e67670f2e88cc3bcf327',  # UIHeroStatisticsItemClass_AutoBind
        'b35ace160b9ba4a4': '683a49b139f7c9142ee9e0555e524bfc5aecd561de78a6533599664404201d6f',  # UIHeroEntryDescriptionSmallItemClass_AutoBind
        'cfba3b86b9ecf1b4': '3a0f547a1340f0a08e75be05850cf434fa8c6d15f853f51175c77fef3638e2fb',  # UIGameAtlasTitleDesItemClass_AutoBind
        '80b31f035d391e31': 'd1c575fd42aa6b3c3b0db2ccc4816ba26a78e5a0dd863886113f9557a6d2009c',  # UIHeroCollectionSingleTextItemClass_AutoBind
        '96f67d0e87e2cf3c': 'bcd1ccf228ac850d18fa7c716c1673aec0e2ea0fb263050f6ebdab52c8c862cd',  # UICollectionHistoryTipClass_AutoBind
        'a6fa6106ada86734': 'ac92ed530ac450ca00fd7e536dfe3f5e688d89c4cbcc7acf48ee0961c695cf96',  # UIGameAtlasSelectHistoryTipClass_AutoBind
        '81ed72585addc8ea': 'a29ea58d6bd71639dd1ab0960e6f7383a418b8e560105b09ebb38140a7609fc8',  # UICommonRewardDetailTipClass_AutoBind
        '61735e7db9c935cf': '5d1797bfe8e9322a5897f242627da02f6a25dd28391168c01603f6a0579027cf',  # UICareerDetailTipClass_AutoBind
        '2901bcce4d2580fc': '667c6a9a2c91123a810d9f21ae0635afbe2541f40013cc7900f22807719317ef',  # UICommonPropDetailsAndQuantitySelectionClass_AutoBind
        'ff95352af7484e98': '7073b6faf56c1a930dd9e3f26b339fc3d279fdc7c81ab06b9102ba24350d6c9f',  # UIAffixDetailItemClass_AutoBind
        '9a01400ec77cef5c': '4c4405f759149bbc2481cdc7fa8855c62aeea537fda10cf6b6af37fe4c6bf028',  # UILordSkinEffectDesItemClass_AutoBind
        'fc9aebc1189f5273': 'a7a2cb08e54a51d565dfc78b9aa5779d25a0e4d69a43412c3d0ad91d50f77324',  # UINewbieKeywordDesItemClass_AutoBind
        '67bace159a7685a7': '8f630200262caffd36f147c27a42350eca75b94cf4714bc54ddcfd1874d666bf',  # UICommonGiftBagTipsClass_AutoBind
        'adebf094a70c3526': 'a95524cd673a87574aec743d290edad29541de81d82f4fde5510f15d8112240b',  # UICommonWanXiangGiftBagTipsClass_AutoBind
        'c13821672fd46395': '8ab80b7be68d3bca93e308eda6dfd942c62a55c8ca80f9baaba8c17746e2937c',  # UICommonSkinChessMergeBuyClass_AutoBind
        '2dc7a975d7212247': '2a7a34261646bf881a38eae3de7fb3b968798d931a392d4fcce54e8453c81549',  # UIGiftMsgEditItemClass_AutoBind
        '2be7555cb78a21ee': '6a41d16bb3a43aba188c7eeaa2eed5419a601cb0de0ffd2d457c9a00fbdc61a5',  # UIGiftCardClass_AutoBind
        'f0b8906bff2de199': '5b903c37a1bf9436eca593a6b5cd11ff6fb9531ffca2f9bdf9aaa9d06ad44124',  # UIGiftStateMessageClass_AutoBind
        '5c7895ca209ea078': '3ee7c011a22c0eb9236c2411edb9c06e59067898e31468496f620f2e3de1359b',  # UIGiftSwitchPlayerInfoClass_AutoBind
        '7e4c783a977a1d88': '353579f0dce0ff203f815abd0a4f1841ab0420b5e0356deb0790c5a7728cee8e',  # UIGiftRecordsRecieveCardPageClass_AutoBind
        '6ceeaa8a218933d8': '2b30ef232a6a3a3ba72b6bc8947fa2ff100fbd974f46f45ed37bc05dd0d165e5',  # UIGiftFavorabilityShowPopClass_AutoBind
        'fac83ca54c3589d7': 'e388aae9ed66065a71c15b08a454b3cc488495c80498929b71323e925634ac46',  # UIFrinedGiftFavorabilityShowPopClass_AutoBind
        '12369f1367f0a3e1': '74e727eb8a4476333b276384520ce8057f7c8dd29d530b2ae0848c436223867e',  # UIGiftSuccessfullyGiftedPopClass_AutoBind
        'd201d03bf13d4cc9': 'e6422995838da31dd64412a49e064bda4dd0aa03cd704824b39ec58615a6568a',  # UIGiftAskPreviewClass_AutoBind
        '8e16601a90d27c48': '464d0fa5187e4d02fb7cef327e483ffa2f2c35145ac5c678bee3636ebaef0e79',  # UIGiftPropRecordDetailsClass_AutoBind
        '2740e35eff01bf6c': '9ff0b35f6437e893debcdae481a9bdeb0f135613b8cdd0729e9c6c1fb28c5725',  # UINoticeRoseClass_AutoBind
        'db02f4462ebe77f4': '6653601ef704adb1c9acec2f4f0f94d120886ca0f1e23535f8e763c51ab479b0',  # UIComomGiftOpenClass_AutoBind
        '881d0ce685c495d4': '5b62db2e7e9f99f02bb79bec3b703a205cdfd68845ebc9646cf66b20082d0d4e',  # UIGiftShareBtnGroupClass_AutoBind
        '060710882e924f3a': '3d35be8205fa56eca47dac14bf31870166c252f89d7f6a5fc42f5df8ddd090bb',  # UISelfWishTextItemClass_AutoBind
        'e59050e5f151eb97': '4b4d9f8a1c653130db4ea45c722b6d61bf1ae97b5656c2d8ea64da5eacfa63a1',  # UIAnswerResultTitleItemClass_AutoBind
        '76edeaaf10a6b3dd': 'b011c43cbbaa5720cfbc5e083ef86d01b54a414b0dc039a42fb7df3897a5d9b5',  # UIAnswerOptionItemClass_AutoBind
        'ebe3e56f595c739d': '61a4fd652620b167d0ec43eebfe5d3c7dd6caaf5ab7cdf43fdb1742fff78bfec',  # UIQuestionItemClass_AutoBind
        '4ab766493785da7b': 'b4bc553f32e69a454f2d27659c692843e0e540838b4e1123fecbdcfa3be4263a',  # UIAnsweringSettlePopClass_AutoBind
        '2068b1fbd6255409': 'c7ab0c6c4b1f1ccd130da5d9c15c86288acf6062267a8b02b4cac1d147f2df5c',  # UIAnsweringSettlePop2Class_AutoBind
        '4785e89a84c4f228': '62c6de9a16cef87e06a83a3d107b14efc81ec2d851d1272e17f60bb8b56ca5e7',  # UIAnsweringSettlePop3Class_AutoBind
        '3475d2bd3c7bc34f': '699b55f1f876b197751fbf34541183df6f88bf539d825cd0550261f5c4a1c58e',  # UIAnsweringSettlePop4Class_AutoBind
        '5c93a193bf91cb21': '90cec15063f197ab9ffa5403d4b3acbe64f2a251550001536b0f041d67f33b0b',  # UIActiveAnswerQustionViewClass_AutoBind
        '829081666cba5854': '6c26dce02fba2eb177a8a3ff471c83a013b90244daff86c7d557742ca219ec9f',  # UIOperationCommonAnsweringSettlePopClass_AutoBind
        'b414dcd68a5c5b04': '4a799309e86e6c22a05d791099df910247dbb4f8a3b61ed1eb7edee15c71fb3b',  # UIAskQuestionItemClass_AutoBind
        'c08c3373fc2efcc1': 'cd18d50d71352a357b3dcf47464c4fd53d25647355aa3c336393f30b2c7d7558',  # UIOperationAnswering_startClass_AutoBind
        'ce913a808626e3b2': 'baaa4196520db862a5f62996536a5f48012f9fe9ac5d0eb76806e4ff3398a857',  # UIOperationAnswering_finishClass_AutoBind
        '85b3a8be686487cc': '7056cc749965ec985bd5d50b0ab595eb6be610f2a8ee0e38d13771c9977c113e',  # UIOperationAnswering_settlementClass_AutoBind
        '969f18cc2cff90de': '8fab884c630dca2bba720d78fdbf322ec92505f514b2e7895883a1f9c420bb99',  # UIKingRoadRankInfoItemClass_AutoBind
        'c586cc186e112d0a': 'bcb4f22c19699d69759c546aef45a84315f9be85a0cd187db929b871edd4c958',  # UISupernovaRewardKingRoadBigItemClass_AutoBind
        'b60bf0852b4ef0b7': '04bd770ea50d1a300a93531065a76a0449101a08082c1c7410eaa87b3d846bea',  # UISupernovaKingRoadLevelUpClass_AutoBind
        '7f154a0b58def1f9': '4751e605abae215a27e680cbb4faaedef213dc919488557924823c60230dde75',  # UISupernovaRewardKingRoadSmallItemClass_AutoBind
        '0138fd646c5befd6': '497badc4712690ec7211f54f5fc497ffc12913aa7521c3574a67146a20d260ee',  # UINewbieGuideDialogTipStyle1Class_AutoBind
        'b6362cc7f8d4fd75': 'a5d8585a41a5a16436862ac3d2b4b66643b223769947aa4bacb36fc1ce8da4bb',  # UINewbieGuideDialogTipStyle2Class_AutoBind
        'ee554626069ebd14': '3f5627df04d4ce97b79be229ce786ac0e84eb18050dad0ce89082d73b071fd80',  # UINewbieGuideDialogTipStyle3Class_AutoBind
        '39b312a0ea9abc12': '13efd07689acb73d6d01edb08355a25206c045f4051f661b7e8f348817597697',  # UINewbieGuideDialogTipClass_AutoBind
        '10deea2fa2c380dd': 'd2880b82be232ffeddcd24c1183d4bbf1a374d94c39e4f8ac9a0c16c9011cc74',  # UINewbieGuideCardsEffectClass_AutoBind
        '61e696e2b9181250': '63b6e6cd3ef3fb06a7a752a4d7f95c71956886be570d88d70bd6d2667ddd3566',  # WidgetNewbieGuideFingerItemArrow_UClass_AutoBind
        'd00f9093dc533ea1': '2c8c836af47dc4f7f38fefd4289bbd121000e9a9c5254c18399637e9cf232361',  # WidgetNewbieGuideFingerItemArrow_DClass_AutoBind
        'e575cd9b7b728129': '03ec7cdb8c47847e4928eddd5842f91871dd785546b552de945d77ecdaf1333a',  # UITabOperationItemClass_AutoBind
        'e84f94273b1460d5': '603f56a5998c14f2162f307f806e832e618db77d67ac4087549695bc4e6cd7f7',  # UIOperationCollectionItemClass_AutoBind
        '91f373327752a1e0': 'ac0f84a51eae1c2fe4f6bd5c0c54c7f00ec77030d16ab95391bb8cdaaac8e428',  # UIOperationCollectionClass_AutoBind
        'af83941515e91927': 'f4bf6bcb24409465080e643430eb46f50a84cca3b2a2b1907c2b1d677af570d7',  # UIOperationInviteGiftsGroupClass_AutoBind
        '77b9fc81c3470133': '1cd1f57ec02e5133565ed4144d55214a347b99a517aa78aad91bd942f59b4908',  # UIOperationMarketingMainClass_AutoBind
        'e86717b262d46080': 'c70ae5f00db42e1fdcec476bfde18df0a5af942d76f7feaf569e8db7d37bbd87',  # UIOperationMarketingBtnClass_AutoBind
        '7f8e76041e0c4fb6': 'fb46e04160aac4cb66f91851dab1767ee57f12adb52c0a2018635198e427f4ec',  # UICheckInTitleClass_AutoBind
        'd4e723380337f58a': '37096ee715352463b3fe14cefad28817b1541fa82263572da05c6f5b06c7d8b3',  # UIReturnCheckInPanelClass_AutoBind
        '194121676922c215': 'e41a0c8e2b0a15136d89951d3cb683922b41201a3dbe2d8d08b086de7c14c98e',  # UIGameCenterDailyRewardEnterClass_AutoBind
        '6e669239ab38e70b': '24389ce1ccd84b30279d4b0cb574564ef7080c0b1e332e12cfebaa6e8d688fc5',  # UILordAttackEffectRightTopItemClass_AutoBind
        '61d4bdb7fa29aa45': '83b0a2c72ee8235c46dd0a731a49a7317091890138d803d24e5624b934a69f5d',  # UICommonTipsAcquireJumpClass_AutoBind
        'c13a898f95fe2ffd': '9f687424e4fa756741b663fdf9718f27d353ca764442bff7e74462de2e9c2dc1',  # UICommonTipsAcquireJumpBtnClass_AutoBind
        '6dda3f308cc24fc2': '350002c392b20430e2fcc481bc423184aa82658181d08f097716a7b747501cbd',  # UICommonTips_InteractionBtnClass_AutoBind
        '88edd7107b83d082': '287fbbabe2bf69ab1988e9e71c44645c504d33b2ec78afbb84a557a3517a81c3',  # UICommonTips_SimpleText1Class_AutoBind
        'bcec0992a7c360c0': 'b5ee10dca36b603c7782129a7cdbc05dfb24affac69e3f9c75f18302f5fd1b5d',  # UICommonTips_SimpleText3Class_AutoBind
        '0f7a973d99e3540f': 'f3b7f5f415ec358ace496a1e543b7be975620471edadc8c2d9176de1f9cdf8f1',  # UICommonTips_TitleText2Class_AutoBind
        'b1ea51f3446bfd41': '43e97cb1ca265e62357ce086dfe7f9b5b35900c3732ddca8708e4f9652e92ae7',  # UICommonTips_TitleDataList3Class_AutoBind
        'c8f06b310932b003': 'dd508bceef2abfc6e90ee9bd2629967fc9659eaec8e5c5c8719d27c1e2c65591',  # UICommonTips_TitleDataList1Class_AutoBind
        'b8a1e29792990ca5': '9ccc6b4f91e6ed70cfc0f80e9d990303c6d9a7cf07ad4085811aa2f016575769',  # UICommonTipOnlyTextClass_AutoBind
        'f8171b2d5d0f1a5f': 'bdd1beeeb204d99535e533e9e84753bb7d997703036a6f19b7a495172f7a31a4',  # UICommonTipClass_AutoBind
        'ed4f07ac2eacc8fe': '5c771f6209953a906f5d1fcf784bc3930e4f01dd384e6c607b5d6f54745de0e6',  # UICommonTips_EquipGridTipsClass_AutoBind
        'f55d1d1110e7a323': 'be476543a0edeaf9a744535a305cafe0bfa1de80e6e9c00c0968dc2ab7e08dbf',  # UICommonEquipTipsItem1Class_AutoBind
        '18df47918a08d023': 'f9360827ddcde522be867ddf1512bd860dc4aa3617aab8d6ec84f8e8a7918175',  # UICommonHelpTipsItemClass_AutoBind
        'bf9fa4b1b1eaa943': '04414b2c04c302059d5021fa01074b4e8d576c78622649e58eb1c26842820831',  # UICommonCouponItemTipClass_AutoBind
        '26c75cb4012fb706': 'f67d88ebeabda85de91670d69b81ec9d98e9b3da645556b0dbeb61001504db59',  # ItemInfoGroupClass_AutoBind
        '2edf6a0c3c595d9e': 'c1ad42265a819c2e0d9a25ae1176153b169140d5144c6efa0139bbe5c81aac0d',  # MergeItemInfoGroupClass_AutoBind
        '02dfb833a7112cc1': 'bb928ef69d437f91e36bcbec882a87b5f773a485930714b06ce32e861765ea47',  # UICommonLingTipsClass_AutoBind
        '0fa9bbc9d82f71fb': '782e3df9ad6a54b6d8d435d676b2a49d85a37dc8ce7603fa041e9d58818f6783',  # UIActiveTaskItemBase (прямые присваивания)
        '131d0f3b0c28c189': 'a932447e7b2894938d8d3716987007e52075eb91eb98c976b79a61c9b9aa986b',  # UIGameAtlasCardInfoLevelDesItemClass (прямые присваивания)
        '1a2363c3ea46b3b1': '03311b6bf7268b2cf864d125a40ccdeffea4b07202e227b0f11e0df82c81df0d',  # UICareerRecieveTitleViewPopClass (прямые присваивания); пин 1.6.0.1, до 1.6.0.1 был 9ac850d65a89...e647 (изменилась только ShowLordTitleWindow, места вставок те же)
        '1ee062f289f5ddc0': '5a92460da80ad87c00889e6f171d6cc6f3b50ec406514f85ccdf9d27d8b21103',  # UIRelationInfoItemClass (прямые присваивания)
        '9783088cc81b5ffe': '988c07dc29cbfb2c904cc3ae730d4d4c7ad3c0bd5ae37bdf5d952c46b74ff419',  # UINewBieGuideEnergyStepTextItemClass (прямые присваивания)
        'a6bdf7338906ec81': '4170667efe705227038e48937f9b499ac9757799d841a3dcf98c5c1c98050dc6',  # UINewbieGuideItem2Class (прямые присваивания)
        'd76888d448ad6da6': '5641a003d01fae78e47826422f97f790d188c97e36e1325038a671ae49a17e92',  # UIRelationInfoItemStyle1Class (прямые присваивания)
        'ec60a062baa3fc70': 'af31587be61ff172f770d1e0a887a5d93e82ca7556f3b705af3757528e09a9f4',  # UIActiveNewOperationClass (прямые присваивания)
        'd987653d5e907e8f': '87e48cfc19c216f9cd6094834cd3b2ea0f39c09dbbcf563acaa49693bf5da038',  # MergeItemInfoGroupClass (прямые присваивания)
        '6fc67a6c9b0fe5de': '0e37c4abfa5daaf21c261ed3c12ba862c23801150971d29545703378d7750d88',  # UILordInforClass (цикл texts[i].text)
        'a91d7cbc079c3bae': '5b6a3679423bb427716f449dcc7c95bc1927f7a7866e0ab4eab0d91729538cba',  # UITalentDescItemClass_AutoBind
        '776b605a16845b08': '79307d9a24ecb9f019815dd8013da5a38656802fd7bd4c4e3906e862fbf40c46',  # UITalentDescItemStyle1Class_AutoBind
        'e841e6b302ea9141': '2320ead634a4795998eca3a135224857d78147464d0ee6e6fb27deb0ce17b32d',  # UIOperationSkillInfoItemClass_AutoBind
        '91dfc3072f340ef5': '68a92b50d5ba8fc0f0c910246135c9e3af1cc838c8ed8cf54249bf146adc2401',  # UICardBPMissionContentOGClass_AutoBind
        'b6bd4e6c2aee8d7e': '0ba17730f82e8ca0519fba0b1fd4dd762bbcb4a45d7f70951687b3865ef5173b',  # UITRDesItemClass_AutoBind
        'c88dc88fea911fbd': 'f4cfa61fbf88fccb6ed4a3e7190f17fc497279a50e4d08089fcfaab827393d68',  # UITRTalentListClass_AutoBind
        'c9256eb614227964': '0046e2f337bbf1a91106eb221ffc43f9118854a5a6b7abc5c5cad52c349c7d8f',  # UITRTalentOutSideClass_AutoBind
        '732c3d2b7f4984d9': '7f477bc89399ba9820753c1f90f308819dca90cc21e86384a514001e829075be',  # UITRLordItemClass_AutoBind
        '2106c6f018b84fc2': '5a8ee6203fafefb86978f5993240f2e9b78e25b8ee1d161725752ff16aa3bf16',  # UITRThreadItemClass_AutoBind
        'f21a1a3e08d7a60c': '25353870c0222616658e6811424fe8be47edd6471063f122b01447e08fc54211',  # UITREffectListClass_AutoBind
        'ae25f9c71181d6b5': '0fb18d06cbc1c90fea95a9df03e6a04c47968d088bb5ec4269985f55959ca4c5',  # UITREffectOutSideClass_AutoBind
        '8d8e384bfdc31839': '5a77a6c03235b8d4dd807cdb0e0be3680e4c104f03c75a1170d8d9034ef42f50',  # UITRThreadOutSideItemClass_AutoBind
        '4e333b1e3c53fccc': 'b98f07cb855c3c528e0c5190a902dadfa07effeb9601cf1ba8626d5259cf20ae',  # UITRThreadOutSideClass_AutoBind
        'e58108993589262b': '66fd532d15f46fc12e843a792d7f8c57156b66a8e83c3543f01370a01215acfd',  # UITROperationHeroListItemClass_AutoBind
        'a2dbdc72166a0ba0': 'f9813c059f867fcde3fdbf416d0b36e3bc4c389d8ab6a288d1a97a9343b123b7',  # UITRLordListOutSideItemClass_AutoBind
        '2c75c6435106ad31': '7f4d930e85cea67e503d466002df93d422bb462c27cae9bfc5b2ce3a78867f8c',  # UITRComposeClass_AutoBind
        'c8e7ae354aa38ac7': '53a8691dd8c3924c60aa2eb4ded83ae56e79256f8c1aac10f685adb7af1a7d12',  # UIAchieveSelectAchievePageClass_AutoBind
        '483b10cd60af387f': '04418c2ab7709952e1fd34f4375e0de98261744ecaa8a7594932da384bdbf4bf',  # UIAchieveCompletionProgressItemClass_AutoBind
        'beb26c52450fb3e0': 'ca266995c328356031bc4058377d16564abae2eeaf1efbe9e85b42216e4527a3',  # UICommonAchieveTipsClass_AutoBind
        '96acd16b8f0eca99': '4191a2276c39f1ecda089d181fd986c8733bb72afd7aa2d3a721fe9e03ea43ce',  # UIAchieveChessboardTipsClass_AutoBind
        'ffb86dbd995cc04f': '4c630ca28042b773abdfa8bda86f95d43ab71343498ecb6aa7329d98782046ee',  # UIHelpContentItemClass_AutoBind
        '6a433e4f6da01cbd': '651cac468d8c57fcd8335068258702b548d45819d2a649aa40e6f04a2a90f492',  # UIHelpContentItem2Class_AutoBind
        '9cd04ce21b288f50': 'f650b686726c9d2a1f3d4502916e2097aa94fb4f8cc76ba502376b9cda1a73d2',  # UIHelpContentSyle_LeftAlignClass_AutoBind
        'a61b8f72a7dd52c0': 'ac0a36e63768315fc5ea182e986f6488d39361cd93f505db7aaf3e2816599ca9',  # UIHelpContentSyle_CenterAlignClass_AutoBind
        '9eaf7dd9fd461beb': 'f4c228a861e366e7914e44b481ad10b021433db30e5e5308b4397f70c413c468',  # UIHelpContentSyle_RightAlignClass_AutoBind
        'f1cf89bbc3f37889': 'd9ee3dc911932554c838bb2cc93791e23c31f7438b886c4b129cbfa04eebd5f6',  # UIHelpContentSmartSyle_LeftAlignClass_AutoBind
        '0a75422b42b1dd99': '17a0f47f811f7ad61791890d1441f2bd186e46628fc3f415b1b1da6cea52f246',  # UIHelpContentSmartSyle_CenterAlignClass_AutoBind
        '4924a220a7bb2cd2': '1e811727efa8ee02b77fcfa3f09adb4bc1805f15fb8c338fa6367b5b03ff2217',  # UIHelpContentSmartSyle_RightAlignClass_AutoBind
        '3c596b344d9403a6': 'b6e71d7890a019ff771310dc673def34076d51f5a45d1393a68fc588c3030acd',  # UICommonHelpSystemClass_AutoBind
        'e94bfc4b409b8579': '4d8f9119a0a0bc9970c4ede73b06de354a8ed758181d5b12b1aff0d3f546461b',  # UICommonHelpSystemGroupClass_AutoBind
        '3b11ce4931e9a7fb': 'e1cba0e7ac687b1cd92ee2f72a19e3a32f9d0971052ffe5f1e2b9916645e371e',  # UICommonHelpSystemVariousStyle2Class_AutoBind
        'f8d64291eeebfd23': '0f380cbb086dfcc49ff7d3c8db7bbd856ad428c85f901a578426105bea7a97e7',  # UICommonHelpSystemWithImageClass_AutoBind
        '6d73052e4b39b6fc': '68485fe81d6d9b69bc13d1064966ca6c858120dd98577c2aabdb49e1fbc89630',  # UIHelpLayoutItemStyle_Smart1Class_AutoBind
        '2b2964bea65ad80c': 'f2647759d88c6c9cb76230fd5933b97acd1dde6dcef54721a604761a6ea77306',  # UICommonTxtItemClass_AutoBind
        'b226a061a393c853': '57dfd92ff552e2a97a24b1ac8db41d02cb4e215dee0d4fd801198bb8f29d735f',  # UICommonTxtItemSmallClass_AutoBind
        '9ab44a93ab16c961': '8109c1b32897ea1d5f59faf468bbe6ffdfdd4e5e13873da2b12a0aa72d545c30',  # UICommonTextWithTitleTipClass_AutoBind
        '9e7b8c63cd472f91': 'ba972f5d8cd4fa122333b717f5401ae0a67229d1b4bb8c1c1b86b2754ef58577',  # UICommonConfirmWithNormalClass_AutoBind
        'a6905215e37c0427': '0aa55c8a96ff37eab5bfb8c80e962058e72d915233f35fba3f9ff01f1742899a',  # UICommonConfirmWithNormalAndTextClass_AutoBind
        '1dc144bdf03b9e0f': 'e99916146ae32af734ede84a4f3fb556a675f748948518f46445f7d7cbb0e604',  # UICommonConfirmWithPriceClass_AutoBind
        '40a34a6dabb4b59f': '89f528f50247106fc03ae5fbf7760401abaeb9fb65b6c7c9c2b5ae5e1d4eaf81',  # UICommonConfirmWithPropClass_AutoBind
        'c6f70b2855b0a19a': '7c2cb519e832daa20057a7e6286c8f47086a8ff603db714e6012d80a9506791f',  # UICommonNativeMsgBoxStyle_MClass_AutoBind
        'b96b50b93d2006cb': 'c17e68ef83cf4d5838d0c334da178d998293626774fc5acdc589906cdf0ca0aa',  # UIEmbattleDesSmallItemClass_AutoBind
        'ad2a8d3e9bb518ec': 'dd368f260cff3174e7f6cdca9bb2f7516a9d1b3510018ad8f8fe1fbea6158894',  # UIEmbattleSmallLordItemClass_AutoBind
        'dbd25926e3f354f4': 'c1874dabc2987c25c3453d507ac3485909e17ae8d675191e07400198a9f4435c',  # UIembattleDesItemClass_AutoBind
    },
}
# Атласы Texture2D BC7 без mip с русскими надписями (kind=bc7-texture, scripts/atlas_text_patch.py).
# Явный белый список: пакет -> {id: SHA-256 оригинала в prime}; пины сняты с WXQ(1) 1.5.0.1. Любой другой id отклоняется.
BC7_TEXTURE_ALLOWED = {
    '4': {
        'f44b2cd418685246': '97abeb12d5ee9a4320f3a94bee464b0be68c05a8524fd75f033f10e527791c27',  # вывеска 公告 512x512
    },
    '19': {
        'c9f49510f2f67402': '52ec813ee7a53cf3c47f80b698f9188f38b3d87484689ec0fad3164c203c9b74',  # LobbyAtlas 1024x1024
        '13c19e4e4486b31c': '51c906dc15084bbf85d986a29945df677627944e66447bfe8bb0d384e4ec8627',  # CareerAtlas 1024x1024
        'd60aedde2d4cbb8c': 'ce081113a99c4258ecc230cb6dbace6af10a596b0bafce67863bd900baea7a9e',  # SeasonAtlas 1024x1024
        'e86df43167ca2143': '34cedad5573ccb44b86114164dc22838b9ea77123dd58e2929199f23dd6b5f08',  # GameCenterAtlas 1024x1024
    },
}
# Размер потока оригинала = размер нагрузки (BC7 без mip: 16 байт на блок 4x4, то есть ширина*высота). Совпадает с размерами в комментариях выше.
BC7_TEXTURE_BYTES = {
    '4': {'f44b2cd418685246': 512 * 512},
    '19': {
        'c9f49510f2f67402': 1024 * 1024,
        '13c19e4e4486b31c': 1024 * 1024,
        'd60aedde2d4cbb8c': 1024 * 1024,
        'e86df43167ca2143': 1024 * 1024,
    },
}
# Пакет 15 (карточные лица 404x564 BC7, 1914 текстур с китайским): пины вынесены в файл, его SHA-256 закреплён здесь; записи сливаются
# в BC7_TEXTURE_ALLOWED (SHA оригинала) и BC7_TEXTURE_BYTES (длина потока). Пакеты 4 и 19 выше не затрагиваются.
# Размер текстуры (ширина, высота) там, где она не квадратная; для остальных atlas_text_patch берёт квадрат.
BC7_TEXTURE_SIZES: dict[str, dict[str, tuple[int, int]]] = {}
BC7_PINS_PATH = PROJECT / 'localization/profiles/bc7-texture-pins-15-1.5.0.1.json'
BC7_PINS_SHA256 = '7e39370a7dd730d6b4091c3a58e4b4369ed4695723592746fe077c7b954c5591'


def _merge_bc7_pins() -> None:
    raw = BC7_PINS_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BC7_PINS_SHA256:
        raise ValueError('BC7 pin file differs from the SHA-256 pinned in the contract')
    for package, rows in json.loads(raw.decode('utf-8'))['pins'].items():
        if package in ('4', '19'):
            raise ValueError('BC7 pin file must not redefine packages 4 and 19')
        for ident, row in rows.items():
            BC7_TEXTURE_ALLOWED.setdefault(package, {})[ident] = row['sha256']
            BC7_TEXTURE_BYTES.setdefault(package, {})[ident] = row['bytes']
            BC7_TEXTURE_SIZES.setdefault(package, {})[ident] = tuple(row['size'])


_merge_bc7_pins()
# Каждая нагрузка lua-script начинается с этой строки (помощник переноса и точечные правки сборщика build_lua_package.py).
LUA_PAYLOAD_MARKER = b'-- ACE_REVIEWED_RU_WORDWRAP_v2'
PACKAGE_ID = re.compile(r'[1-9][0-9]{0,9}')
PRIME_FILE = re.compile(r'(0|[1-9][0-9]{0,9})/\1(?:_[0-9]{1,3})?\.db')
RESOURCE_ID = re.compile(r'[0-9a-f]{16}')
SHA256 = re.compile(r'[0-9a-f]{64}')
_MOUNT = re.compile(r'MountPackage (\d+), prime, \d+ => succeed! .*?pkgHash:(\d+) basePkgHash:(\d+)')
REQUIRED_KEYS = (
    'schema_version', 'status', 'resource_contract', 'game_version', 'packages', 'prime_pkg_hashes',
    'resources', 'files', 'source_copies', 'override_package_headers_hex', 'official_cache_infos',
    'prime_global_index_sha256', 'native_sdk_hashes', 'native_verification', 'native_fallback_reads',
    'actual_override_root', 'headers_equal_observed_runtime_cache', 'complete_prime_global_index_unchanged',
    'deletion_records', 'vendor_diff_called',
)


def prime_pkg_hashes(log_text: str) -> dict[str, str]:
    """pkgHash prime-пакетов из QtsVFS.log; противоречивые или неравные basePkgHash — ошибка."""
    found: dict[str, str] = {}
    for package, pkg_hash, base_hash in _MOUNT.findall(log_text):
        if pkg_hash != base_hash:
            raise ValueError(f'QtsVFS.log: package {package} pkgHash differs from basePkgHash')
        if found.setdefault(package, pkg_hash) != pkg_hash:
            raise ValueError(f'QtsVFS.log: package {package} has several pkgHash values')
    return found


def read_prime_pkg_hashes(log_path: Path) -> dict[str, str]:
    if not log_path.is_file():
        raise ValueError(f'QtsVFS.log not found, prime pkgHash cannot be confirmed: {log_path}')
    return prime_pkg_hashes(log_path.read_text('utf-8', errors='replace'))


def installed_info(cache_root: Path, package: str) -> dict:
    path = cache_root / package / (package + '.info')
    try:
        data = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise ValueError(f'Official cache INFO is unreadable for package {package}') from error
    if not isinstance(data, dict) or not isinstance(data.get('version'), str):
        raise ValueError(f'Official cache INFO of package {package} has no version')
    return data


def candidate_packages(manifest: dict) -> list[str]:
    packages = sorted({row['package'] for row in manifest['resources']}, key=int)
    if not packages or any(not PACKAGE_ID.fullmatch(p) for p in packages):
        raise ValueError('Candidate resources name no valid packages')
    return packages


def require_shape(manifest: dict) -> list[str]:
    """Структура манифеста пилотного контракта; возвращает пакеты кандидата."""
    for key in REQUIRED_KEYS:
        if key not in manifest:
            raise ValueError(f'Pilot candidate manifest lacks {key}')
    if manifest['schema_version'] != SCHEMA_VERSION or manifest['status'] != STATUS or manifest['resource_contract'] != CONTRACT:
        raise ValueError('Not a pilot candidate manifest')
    packages = candidate_packages(manifest)
    if manifest['packages'] != packages or set(manifest['prime_pkg_hashes']) != set(packages):
        raise ValueError('Manifest package list differs from its resources')
    if not manifest['resources'] or any(not isinstance(r, dict) for r in manifest['resources']):
        raise ValueError('Pilot candidate has no resources')
    seen = set()
    for row in manifest['resources']:
        identity = (row.get('package'), row.get('id_hex'))
        if not RESOURCE_ID.fullmatch(str(row.get('id_hex'))) or identity in seen or row.get('kind') not in KINDS:
            raise ValueError('Invalid or duplicate pilot resource identity/kind')
        seen.add(identity)
        if not SHA256.fullmatch(str(row.get('sha256'))) or not SHA256.fullmatch(str(row.get('original_sha256'))):
            raise ValueError('Pilot resource hashes are invalid')
    require_raw_replacements(manifest)
    require_lua_replacements(manifest)
    require_bc7_textures(manifest)
    require_text_resources(manifest)
    for package in packages:
        header = manifest['override_package_headers_hex'].get(package)
        info = manifest['official_cache_infos'].get(package)
        if not isinstance(header, str) or not header or not isinstance(info, dict) or not isinstance(info.get('data'), dict):
            raise ValueError(f'Manifest lacks header/INFO for package {package}')
        if not re.fullmatch(r'[0-9]{1,20}', str(manifest['prime_pkg_hashes'][package])):
            raise ValueError(f'Manifest pkgHash of package {package} is invalid')
    require_prime_rows(manifest['source_copies'], packages)
    if manifest['native_sdk_hashes'] is None or not all(SHA256.fullmatch(str(v)) for v in manifest['native_sdk_hashes'].values()):
        raise ValueError('Manifest lacks native SDK pins')
    return packages


def require_raw_replacements(manifest: dict) -> None:
    """Ресурсы kind=raw-replacement: только из белого списка, заранее заявлены в manifest['raw_replacements'], SHA совпадают."""
    resources = [r for r in manifest['resources'] if r.get('kind') == 'raw-replacement']
    declared = manifest.get('raw_replacements', [])
    if not isinstance(declared, list) or any(not isinstance(d, dict) for d in declared):
        raise ValueError('Manifest raw_replacements is invalid')
    by_id = {}
    for row in declared:
        key = (row.get('package'), row.get('id_hex'))
        if key in by_id:
            raise ValueError('Duplicate raw replacement declaration')
        by_id[key] = row
    if len(by_id) != len(resources):
        raise ValueError('Raw replacement resources and declarations differ')
    for resource in resources:
        key = (resource['package'], resource['id_hex'])
        expected_original = RAW_REPLACEMENT_ALLOWED.get(key[0], {}).get(key[1])
        if expected_original is None:
            raise ValueError(f'Resource {key[0]}/{key[1]} is not on the raw replacement allowlist')
        row = by_id.get(key)
        if row is None or row.get('kind') != 'raw-replacement':
            raise ValueError(f'Raw replacement {key[0]}/{key[1]} is not declared in the manifest')
        if resource['original_sha256'] != expected_original or row.get('original_sha256') != expected_original:
            raise ValueError(f'Raw replacement {key[0]}/{key[1]} original differs from the pinned prime resource')
        if row.get('sha256') != resource['sha256'] or not isinstance(row.get('bytes'), int) or row['bytes'] <= 0:
            raise ValueError(f'Raw replacement {key[0]}/{key[1]} declaration differs from the resource row')


def require_lua_replacements(manifest: dict) -> None:
    """Ресурсы kind=lua-script: только из белого списка LUA_REPLACEMENT_ALLOWED, заявлены в manifest['lua_replacements'], SHA оригиналов закреплены."""
    resources = [r for r in manifest['resources'] if r.get('kind') == 'lua-script']
    declared = manifest.get('lua_replacements', [])
    if not isinstance(declared, list) or any(not isinstance(d, dict) for d in declared):
        raise ValueError('Manifest lua_replacements is invalid')
    by_id = {}
    for row in declared:
        key = (row.get('package'), row.get('id_hex'))
        if key in by_id:
            raise ValueError('Duplicate Lua replacement declaration')
        by_id[key] = row
    if len(by_id) != len(resources):
        raise ValueError('Lua replacement resources and declarations differ')
    for resource in resources:
        key = (resource['package'], resource['id_hex'])
        expected_original = LUA_REPLACEMENT_ALLOWED.get(key[0], {}).get(key[1])
        if expected_original is None:
            raise ValueError(f'Resource {key[0]}/{key[1]} is not on the Lua replacement allowlist')
        row = by_id.get(key)
        if row is None or row.get('kind') != 'lua-script':
            raise ValueError(f'Lua replacement {key[0]}/{key[1]} is not declared in the manifest')
        if resource['original_sha256'] != expected_original or row.get('original_sha256') != expected_original:
            raise ValueError(f'Lua replacement {key[0]}/{key[1]} original differs from the pinned prime resource')
        if row.get('sha256') != resource['sha256'] or not isinstance(row.get('bytes'), int) or row['bytes'] <= 0:
            raise ValueError(f'Lua replacement {key[0]}/{key[1]} declaration differs from the resource row')


def require_bc7_textures(manifest: dict) -> None:
    """Ресурсы kind=bc7-texture: только из белого списка BC7_TEXTURE_ALLOWED, SHA оригинала закреплён.

    Размер потока в манифесте не записан, поэтому его равенство оригиналу проверяет require_bc7_stream_size
    (validate_carrier вызывает её для каждой нагрузки кандидата)."""
    for resource in manifest['resources']:
        if resource.get('kind') != 'bc7-texture':
            continue
        key = (resource['package'], resource['id_hex'])
        expected_original = BC7_TEXTURE_ALLOWED.get(key[0], {}).get(key[1])
        if expected_original is None:
            raise ValueError(f'Resource {key[0]}/{key[1]} is not on the BC7 texture allowlist')
        if resource['original_sha256'] != expected_original:
            raise ValueError(f'BC7 texture {key[0]}/{key[1]} original differs from the pinned prime resource')


def require_bc7_stream_size(resource: dict, payload: bytes) -> None:
    """Поток BC7-текстуры той же длины, что у оригинала (len(payload) == len(original)); длина оригинала закреплена в BC7_TEXTURE_BYTES."""
    expected = BC7_TEXTURE_BYTES.get(resource['package'], {}).get(resource['id_hex'])
    if expected is None:
        raise ValueError(f'BC7 texture {resource["package"]}/{resource["id_hex"]} has no pinned stream size')
    if len(payload) != expected:
        raise ValueError(f'BC7 texture {resource["package"]}/{resource["id_hex"]} stream is {len(payload)} bytes, original is {expected}')


@functools.lru_cache(maxsize=1)
def text_resource_pins() -> dict:
    """{пакет: {id: SHA-256 оригинала}}; файл обязан совпасть с закреплённым в коде SHA-256."""
    raw = TEXT_RESOURCE_PINS_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEXT_RESOURCE_PINS_SHA256:
        raise ValueError('Text resource pin file differs from the SHA-256 pinned in the contract')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or any(not isinstance(rows, dict) for rows in value.values()):
        raise ValueError('Text resource pin file has an invalid shape')
    return value


def require_text_resources(manifest: dict) -> None:
    """Ресурсы kind=pb-table-text / unity-text: пакет и оригинал из белого списка пинов, заявлены в манифесте со своими правками."""
    for kind, (package, key) in TEXT_KINDS.items():
        resources = [r for r in manifest['resources'] if r.get('kind') == kind]
        declared = manifest.get(key, [])
        if not isinstance(declared, list) or any(not isinstance(d, dict) for d in declared):
            raise ValueError(f'Manifest {key} is invalid')
        by_id = {}
        for row in declared:
            ident = (row.get('package'), row.get('id_hex'))
            if ident in by_id:
                raise ValueError(f'Duplicate {key} declaration')
            by_id[ident] = row
        if len(by_id) != len(resources):
            raise ValueError(f'{kind} resources and {key} declarations differ')
        for resource in resources:
            ident = (resource['package'], resource['id_hex'])
            if ident[0] != package:
                raise ValueError(f'{kind} resource {ident[0]}/{ident[1]} is outside package {package}')
            pinned = text_resource_pins().get(ident[0], {}).get(ident[1])
            if pinned is None:
                raise ValueError(f'Resource {ident[0]}/{ident[1]} is not on the text resource pin list')
            row = by_id.get(ident)
            if row is None or resource['original_sha256'] != pinned or row.get('original_sha256') != pinned:
                raise ValueError(f'{kind} {ident[0]}/{ident[1]} original differs from the pinned prime resource')
            if row.get('sha256') != resource['sha256'] or not isinstance(row.get('edits'), list) or not row['edits']:
                raise ValueError(f'{kind} {ident[0]}/{ident[1]} declaration differs from the resource row')
            for edit in row['edits']:
                if not isinstance(edit, dict) or not SHA256.fullmatch(str(edit.get('source_sha256'))) or not SHA256.fullmatch(str(edit.get('sha256'))):
                    raise ValueError(f'{kind} {ident[0]}/{ident[1]} has an edit without valid hashes')
                if kind == 'pb-table-text' and not isinstance(edit.get('address'), str):
                    raise ValueError(f'{kind} {ident[0]}/{ident[1]} has an edit without address')
                if kind == 'unity-text' and (type(edit.get('path_id')) is not int or not isinstance(edit.get('field'), str)):
                    raise ValueError(f'{kind} {ident[0]}/{ident[1]} has an edit without path_id/field')


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def prove_text_resource(kind: str, declaration: dict, original: bytes, payload: bytes) -> None:
    """Заново доказывает кодеком, что payload отличается от оригинала только заявленными текстами; манифесту не доверяет.

    Тексты правок берутся из самого payload; хеши источника и перевода обязаны совпасть с манифестом."""
    for path in (str(PROJECT), str(PROJECT / 'scripts')):
        if path not in sys.path:
            sys.path.insert(0, path)
    edits = declaration['edits']
    if kind == 'pb-table-text':
        from localization import pb_table_codec as codec
        try:
            before, after = codec.parse(original), codec.parse(payload)
            found = {}
            for edit in edits:
                address = codec.normalize_address(edit['address'])
                old, new = before.index.get(address), after.index.get(address)
                if address in found or old is None or new is None or old.kind != 'text' or new.kind != 'text':
                    raise ValueError(f'Table text edit {edit["address"]}: leaf is missing or duplicated')
                if _digest(old.text) != edit['source_sha256'] or _digest(new.text) != edit['sha256']:
                    raise ValueError(f'Table text edit {address}: hashes differ from the manifest')
                found[address] = new.text
            codec.prove_text_only(original, payload, found)
        except codec.PbTableError as error:
            raise ValueError(f'Table text proof failed: {error}') from error
        return
    from localization import unity_text_codec as codec
    keys = [(edit['path_id'], edit['field']) for edit in edits]
    if len(set(keys)) != len(keys):
        raise ValueError('Unity text edits are duplicated')
    try:
        allowed = {(pid, path) for pid, path, _ in codec.list_text_fields(original)}
        if any(key not in allowed for key in keys):
            raise ValueError('Unity text edit targets a field that is not a CJK text field')
        old_values, new_values = codec.read_field_values(original, keys), codec.read_field_values(payload, keys)
        for edit, key in zip(edits, keys):
            if _digest(old_values[key]) != edit['source_sha256'] or _digest(new_values[key]) != edit['sha256']:
                raise ValueError(f'Unity text edit {key}: hashes differ from the manifest')
        codec.prove_text_only(original, payload, new_values)
    except codec.UnityTextError as error:
        raise ValueError(f'Unity text proof failed: {error}') from error


def make_prime_reader(game: Path, container_cls):
    """Читает ресурс пакета из prime-файлов установки (только чтение): read(package, id_int) -> bytes | None."""
    def read(package: str, ident: int):
        folder = game / 'WXQ_Data/Package/builtin' / package
        for path in sorted(folder.glob(f'{package}_*.db'), key=lambda p: int(p.stem.rsplit('_', 1)[1])):
            with container_cls(path) as container:
                if ident in container.entries:
                    return container.decode_entry(ident)
        return None
    return read


def require_prime_rows(rows: list[dict], packages: list[str]) -> None:
    """У каждого пакета есть каталог `<p>.db` и хотя бы один файл данных; чужих файлов нет."""
    relatives = [row['relative'] for row in rows]
    if len(set(relatives)) != len(relatives) or any(not PRIME_FILE.fullmatch(r) for r in relatives):
        raise ValueError('Prime source file rows are invalid or duplicated')
    for package in packages:
        own = {r for r in relatives if r.split('/')[0] == package}
        if f'{package}/{package}.db' not in own or len(own) < 2:
            raise ValueError(f'Prime source rows of package {package} are incomplete')
    if {r.split('/')[0] for r in relatives} - set(packages) - {'0'}:
        raise ValueError('Prime source rows name packages outside the candidate')


HEADER_BYTES = 120
HEADER_HASH_OFFSETS = (('pkgHash', 80), ('basePkgHash', 88))


def header_hashes(header_hex: str) -> dict[str, str]:
    """pkgHash/basePkgHash из 120-байтного override-заголовка: два u32 LE (старшая, затем младшая часть)."""
    header = bytes.fromhex(header_hex)
    if len(header) != HEADER_BYTES:
        raise ValueError('Override header is not 120 bytes')
    return {name: str((int.from_bytes(header[o:o + 4], 'little') << 32) | int.from_bytes(header[o + 4:o + 8], 'little')) for name, o in HEADER_HASH_OFFSETS}


def check_identity(manifest: dict, cache_root: Path, log_path: Path) -> None:
    """Кандидат собран для этой версии и этих prime-пакетов: info, pkgHash (по QtsVFS.log)."""
    packages = require_shape(manifest)
    log_hashes = read_prime_pkg_hashes(log_path)
    for package in packages:
        wanted = manifest['official_cache_infos'][package]['data'].get('version')
        installed = installed_info(cache_root, package).get('version')
        if wanted != manifest['game_version'] or installed != wanted:
            raise ValueError(f'Candidate was built for game version {wanted}; package {package} is {installed} here')
        if log_hashes.get(package) != str(manifest['prime_pkg_hashes'][package]):
            raise ValueError(f'Prime pkgHash of package {package} differs from QtsVFS.log: candidate is for another build')
        inside = header_hashes(manifest['override_package_headers_hex'][package])
        if set(inside.values()) != {log_hashes[package]}:
            raise ValueError(f'Base hash inside the override header of package {package} differs from the prime pkgHash')


def validate_carrier(directory: Path, manifest: dict, container_cls, header_id: int, prime_reader=None) -> None:
    """Сами файлы кандидата: ровно заявленные ресурсы, заголовок, SHA полезной нагрузки.

    prime_reader (см. make_prime_reader) нужен для текстовых ресурсов: оригинал берётся из prime и сверяется с пином, правки доказываются заново."""
    for package in candidate_packages(manifest):
        rows = [r for r in manifest['resources'] if r['package'] == package]
        ids = {int(r['id_hex'], 16) for r in rows}
        with container_cls(directory / 'packages' / package / (package + '.db'), raw_values=True) as metadata, \
                container_cls(directory / 'packages' / package / (package + '_0.db')) as data:
            if set(metadata.entries) != ids | {header_id} or set(data.entries) != ids or metadata.invalid_record_count or data.invalid_record_count:
                raise ValueError('Pilot carrier has extra, missing or deletion records')
            if metadata.raw_record_values(header_id) != [bytes.fromhex(manifest['override_package_headers_hex'][package])]:
                raise ValueError('Pilot carrier header differs from the manifest')
            for row in rows:
                identity = int(row['id_hex'], 16)
                payload = data.decode_entry(identity)
                if hashlib.sha256(payload).hexdigest() != row['sha256']:
                    raise ValueError('Pilot carrier payload hash mismatch')
                if row['kind'] == 'bc7-texture':
                    require_bc7_stream_size(row, payload)
                if row['kind'] in TEXT_KINDS:
                    if prime_reader is None:
                        raise ValueError('Text resources need the prime reader to be re-proved')
                    original = prime_reader(package, identity)
                    pinned = text_resource_pins().get(package, {}).get(row['id_hex'])
                    if original is None or pinned is None or hashlib.sha256(original).hexdigest() != pinned or row['original_sha256'] != pinned:
                        raise ValueError(f'Text resource {package}/{row["id_hex"]}: prime original differs from the pin')
                    declaration = next(d for d in manifest[TEXT_KINDS[row['kind']][1]] if (d['package'], d['id_hex']) == (package, row['id_hex']))
                    prove_text_resource(row['kind'], declaration, original, payload)
                if row['kind'] == 'lua-script':
                    try:
                        payload.decode('utf-8')
                    except UnicodeDecodeError as error:
                        raise ValueError('Lua replacement is not UTF-8 text') from error
                    if not payload.startswith(LUA_PAYLOAD_MARKER):
                        raise ValueError('Lua replacement lacks the reviewed word-wrap marker')
                values = metadata.raw_record_values(identity)
                if len(values) != 1 or len(values[0]) != 56:
                    raise ValueError('Invalid or duplicate native metadata in pilot carrier')
                coordinates = [(c.main_block, c.sub_block) for c in data.entries[identity]]
                if len(set(coordinates)) != len(coordinates):
                    raise ValueError('Duplicate native chunk identity in pilot carrier')


def expected_reads(manifest: dict) -> dict[str, str]:
    reads = dict(manifest['native_fallback_reads'])
    reads.update({row['id_hex']: row['sha256'] for row in manifest['resources']})
    return reads
